//! Single owner-aware scheduler. Resource payloads stay immutable and shared.
use crate::resources::Resources;
use serde::Serialize;
use std::{
    collections::{BTreeMap, BTreeSet},
    sync::Arc,
    time::{Duration, Instant},
};
use terminal_core::resources::{ResourcePayload, ResourceRequest};
use tokio::{
    sync::{broadcast, mpsc, oneshot, watch},
    task::{AbortHandle, JoinHandle, JoinSet},
};
#[derive(Clone, Serialize)]
pub struct ResourceUpdate {
    pub owner: String,
    pub generation: u64,
    pub revision: u64,
    pub request: ResourceRequest,
    pub payload: Option<Arc<ResourcePayload>>,
    pub error: Option<String>,
    pub loading: bool,
}
#[derive(Clone)]
pub struct ResourceNotification {
    pub owners: Vec<String>,
}
enum Command {
    Subscribe(
        String,
        ResourceRequest,
        oneshot::Sender<Result<ResourceUpdate, String>>,
    ),
    Unsubscribe(String, u64, oneshot::Sender<()>),
}
#[derive(Clone)]
pub struct Subscriptions {
    commands: mpsc::Sender<Command>,
    latest: watch::Receiver<Arc<BTreeMap<String, ResourceUpdate>>>,
    notifications: broadcast::Sender<ResourceNotification>,
    stop: watch::Sender<bool>,
}
impl Subscriptions {
    pub async fn subscribe(
        &self,
        owner: String,
        request: ResourceRequest,
    ) -> Result<ResourceUpdate, String> {
        if owner.is_empty() || owner.len() > 384 || owner.chars().any(char::is_control) {
            return Err("Invalid subscription owner".into());
        }
        request.validate().map_err(|e| e.to_string())?;
        let (tx, rx) = oneshot::channel();
        self.commands
            .try_send(Command::Subscribe(owner, request, tx))
            .map_err(|e| e.to_string())?;
        rx.await.map_err(|e| e.to_string())?
    }
    pub async fn unsubscribe(&self, owner: String, generation: u64) -> Result<(), String> {
        let (tx, rx) = oneshot::channel();
        self.commands
            .try_send(Command::Unsubscribe(owner, generation, tx))
            .map_err(|e| e.to_string())?;
        rx.await.map_err(|e| e.to_string())
    }
    pub fn recover(&self) -> Arc<BTreeMap<String, ResourceUpdate>> {
        self.latest.borrow().clone()
    }
    pub fn notifications(&self) -> broadcast::Receiver<ResourceNotification> {
        self.notifications.subscribe()
    }
    pub fn stop(&self) {
        self.stop.send_replace(true);
    }
}
struct Entry {
    owners: BTreeSet<String>,
    payload: Option<Arc<ResourcePayload>>,
    error: Option<String>,
    due: Instant,
    abort: Option<AbortHandle>,
    generation: u64,
}
struct Owner {
    request: ResourceRequest,
    generation: u64,
}
type Completion = (
    ResourceRequest,
    u64,
    Result<Arc<ResourcePayload>, String>,
    Instant,
);
pub fn start(resources: Resources) -> (Subscriptions, JoinHandle<()>) {
    let (commands, mut receiver) = mpsc::channel(4352);
    let (latest_tx, latest) = watch::channel(Arc::new(BTreeMap::new()));
    let (notifications, _) = broadcast::channel(64);
    let (stop, mut stopped) = watch::channel(false);
    let handle = Subscriptions {
        commands,
        latest,
        notifications: notifications.clone(),
        stop,
    };
    let task = tokio::spawn(async move {
        let mut entries = BTreeMap::<ResourceRequest, Entry>::new();
        let mut owners = BTreeMap::<String, Owner>::new();
        let mut tasks = JoinSet::<Completion>::new();
        let mut generation = 0u64;
        let mut revision = 0u64;
        let mut timer = tokio::time::interval(Duration::from_millis(100));
        timer.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
        loop {
            if *stopped.borrow() {
                break;
            }
            tokio::select! {
                _=stopped.changed()=>break,
                command=receiver.recv()=>match command {
                    Some(Command::Subscribe(owner,request,reply))=> {
                        if !owners.contains_key(&owner) && owners.len()>=4352 { let _=reply.send(Err("Subscription capacity reached".into())); continue; }
                        if let Some(old)=owners.remove(&owner) { release(&owner,&old.request,&mut entries); }
                        generation=generation.wrapping_add(1); let owner_generation=generation;
                        let entry=entries.entry(request.clone()).or_insert_with(||Entry{owners:BTreeSet::new(),payload:None,error:None,due:Instant::now(),abort:None,generation:owner_generation});
                        entry.owners.insert(owner.clone()); owners.insert(owner.clone(),Owner{request,generation:owner_generation});
                        publish(&owners,&entries,&mut revision,&latest_tx,&notifications,vec![owner.clone()]);
                        let update=latest_tx.borrow().get(&owner).cloned().ok_or_else(||"Subscription unavailable".to_owned()); let _=reply.send(update);
                    }
                    Some(Command::Unsubscribe(owner,expected,reply))=> { if owners.get(&owner).is_some_and(|old|old.generation==expected) { if let Some(old)=owners.remove(&owner){release(&owner,&old.request,&mut entries);} publish(&owners,&entries,&mut revision,&latest_tx,&notifications,vec![]); } let _=reply.send(()); }
                    None=>break,
                },
                completion=tasks.join_next(), if !tasks.is_empty()=> {
                    if let Some(Ok((request,version,result,due)))=completion { if let Some(entry)=entries.get_mut(&request) { if entry.generation==version { entry.abort=None;entry.due=due; match result { Ok(payload)=>{entry.payload=Some(payload);entry.error=None;},Err(error)=>entry.error=Some(error) } let changed=entry.owners.iter().cloned().collect();publish(&owners,&entries,&mut revision,&latest_tx,&notifications,changed); } } }
                },
                _=timer.tick()=> {
                    for (request,entry) in entries.iter_mut() {
                        if tasks.len()>=4 { break; }
                        if entry.abort.is_some() || entry.due>Instant::now() {continue;}
                        let resources=resources.clone();let request=request.clone();let version=entry.generation;
                        entry.abort=Some(tasks.spawn(async move{ let result=resources.request(request.clone()).await;let due=resources.next_due(&request).await;(request,version,result,due) }));
                    }
                }
            }
        }
        tasks.abort_all();
        while tasks.join_next().await.is_some() {}
    });
    (handle, task)
}
fn release(owner: &str, request: &ResourceRequest, entries: &mut BTreeMap<ResourceRequest, Entry>) {
    if let Some(entry) = entries.get_mut(request) {
        entry.owners.remove(owner);
        if entry.owners.is_empty() {
            if let Some(abort) = &entry.abort {
                abort.abort();
            }
            entries.remove(request);
        }
    }
}
fn publish(
    owners: &BTreeMap<String, Owner>,
    entries: &BTreeMap<ResourceRequest, Entry>,
    revision: &mut u64,
    latest: &watch::Sender<Arc<BTreeMap<String, ResourceUpdate>>>,
    notifications: &broadcast::Sender<ResourceNotification>,
    changed: Vec<String>,
) {
    *revision = revision.wrapping_add(1);
    let map = owners
        .iter()
        .filter_map(|(id, owner)| {
            entries.get(&owner.request).map(|entry| {
                (
                    id.clone(),
                    ResourceUpdate {
                        owner: id.clone(),
                        generation: owner.generation,
                        revision: *revision,
                        request: owner.request.clone(),
                        payload: entry.payload.clone(),
                        error: entry.error.clone(),
                        loading: entry.payload.is_none() && entry.error.is_none(),
                    },
                )
            })
        })
        .collect();
    latest.send_replace(Arc::new(map));
    let _ = notifications.send(ResourceNotification { owners: changed });
}
#[cfg(test)]
mod tests {
    use super::*;
    use terminal_core::InstrumentId;
    use terminal_providers::{MockProvider, Provider};
    #[tokio::test]
    async fn shared_owners_rebinding_visibility_and_lag_recovery(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let (handle, task) = start(Resources::new(Provider::Mock(MockProvider::new(42))));
        let quote = ResourceRequest::Quote {
            instrument: InstrumentId::parse("AAPL")?,
        };
        let first = handle.subscribe("chart-a".into(), quote.clone()).await?;
        let second = handle.subscribe("chart-b".into(), quote.clone()).await?;
        tokio::time::timeout(Duration::from_secs(3), async {
            loop {
                let state = handle.recover();
                if let (Some(a), Some(b)) = (
                    state.get("chart-a").and_then(|s| s.payload.as_ref()),
                    state.get("chart-b").and_then(|s| s.payload.as_ref()),
                ) {
                    assert!(Arc::ptr_eq(a, b));
                    break;
                }
                tokio::time::sleep(Duration::from_millis(5)).await;
            }
        })
        .await?;
        handle
            .unsubscribe("chart-a".into(), first.generation)
            .await?;
        assert!(handle.recover().contains_key("chart-b"));
        let bitcoin = ResourceRequest::Quote {
            instrument: InstrumentId::parse("coingecko:bitcoin")?,
        };
        let rebound = handle.subscribe("chart-b".into(), bitcoin.clone()).await?;
        assert!(rebound.generation > second.generation);
        handle
            .unsubscribe("chart-b".into(), second.generation)
            .await?;
        assert_eq!(
            handle.recover().get("chart-b").map(|s| &s.request),
            Some(&bitcoin)
        );
        tokio::time::timeout(Duration::from_secs(3),async{loop{let state=handle.recover();if let Some(payload)=state.get("chart-b").and_then(|s|s.payload.as_ref()){assert!(matches!(payload.as_ref(),ResourcePayload::Quote(market) if market.data.as_ref().is_some_and(|q|q.instrument.id.as_str()=="coingecko:bitcoin")));break;}tokio::time::sleep(Duration::from_millis(5)).await;}}).await?;
        handle
            .unsubscribe("chart-b".into(), rebound.generation)
            .await?;
        assert!(handle.recover().is_empty());
        let resumed = handle.subscribe("chart-b".into(), bitcoin).await?;
        assert!(resumed.generation > rebound.generation);
        let mut notifications = handle.notifications();
        for i in 0..70 {
            handle
                .subscribe(format!("owner-{i}"), quote.clone())
                .await?;
        }
        assert!(matches!(
            notifications.recv().await,
            Err(broadcast::error::RecvError::Lagged(_))
        ));
        assert_eq!(handle.recover().len(), 71);
        handle.stop();
        task.await?;
        assert!(handle.subscribe("closed".into(), quote).await.is_err());
        Ok(())
    }
}
