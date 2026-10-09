"""Run the approved isolated 20-case campaign against the real application."""
import json, time, uuid, statistics, subprocess
from pathlib import Path
import requests

CASES = [
("maybank", "Research Maybank 1155.KL as of today: business, latest annual and interim results, risks and recommendations. Use verified data and the standard report sections."),
("mynews", "Analyze Mynews 5275.KL latest financial statements. Preserve exact base MYR amounts, EPS per share and fiscal period ends. Use standard report sections."),
("ytl", "Resolve YTL Corporation versus YTL Power, research YTL Corporation only, and explain symbol identity and latest evidence using standard report sections."),
("malaysia_macro", "Research current Malaysia GDP growth, inflation and BNM OPR with publication dates, limitations and standard report sections."),
("comparison", "Compare latest Maybank and CIMB financial results using like-for-like periods, verified numbers and standard report sections."),
("news", "Research Maybank news from the last seven days. Distinguish dated company news from undated pages and provide standard report sections."),
("historical", "Research Maybank FY2023 and Q1 2024 only. Preserve historical scope and do not substitute current statements. Use standard report sections."),
("rust_ratios", "Research AAPL latest annual financial ratios. Use registered native calculation tools if supported; disclose missing inputs and provide standard report sections."),
("unavailable", "Research a company with ticker ZZZZZINVALID. Verify identity first; if unavailable, explain the failure, do not invent data, and use standard report sections."),
("supplied_data", "Analyze only these supplied figures, without fetching providers: fictional Example Retail, annual period ending 2025-12-31, revenue MYR 878460000, net profit MYR 17840000; interim period ending 2026-04-30, revenue MYR 225970000, net profit MYR 55000, diluted EPS MYR 0.0001. Do not invent quarter number or audited status. Use standard report sections.")]

HEADINGS = ["Executive Summary", "Analysis", "Key Risks", "Recommendations", "Sources"]
def main():
 campaign = uuid.uuid4().hex
 root = Path('.review-tmp/evaluation') / campaign
 root.mkdir(parents=True)
 meta = {"campaign": campaign, "started_at": time.strftime('%Y-%m-%dT%H:%M:%S%z'),
         "revision": subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
         "dirty_tree": bool(subprocess.check_output(['git','status','--porcelain'],text=True)),
         "cost_usd": None, "cost_note": "Pricing unavailable; report actual token usage", "runs": []}
 (root/'results.json').write_text(json.dumps(meta,indent=2))
 infrastructure_streak = 0
 for repeat in range(2):
  for name,prompt in CASES:
   started=time.monotonic(); events=[]; first_token=None; first_result=None; error=None
   thread=f"eval-{campaign}-{repeat}-{name}"
   try:
    with requests.post('http://127.0.0.1:8000/api/chat/stream',json={"message":prompt,"thread_id":thread,"org_id":"evaluation","user_id":"evaluation","response_schema":"fincept"},stream=True,timeout=(10,3605)) as response:
     response.raise_for_status()
     for line in response.iter_lines():
      if not line.startswith(b'data: '):continue
      event=json.loads(line[6:]);event['elapsed_s']=round(time.monotonic()-started,3);events.append(event)
      if event.get('type')=='token' and first_token is None:first_token=event['elapsed_s']
      if event.get('type')=='tool_result' and first_result is None:first_result=event['elapsed_s']
    infrastructure_streak=0
   except requests.RequestException as exc:
    error=type(exc).__name__;infrastructure_streak+=1
   done=next((e for e in reversed(events) if e.get('type')=='done'),{})
   budget=next((e for e in reversed(events) if e.get('type')=='budget'),{})
   report=done.get('result','');calls=[e for e in events if e.get('type')=='tool_call'];results=[e for e in events if e.get('type')=='tool_result'];failures=[e for e in results if e.get('status')=='error']
   row={"case":name,"repeat":repeat+1,"thread_id":thread,"duration_s":round(time.monotonic()-started,3),"time_to_first_token_s":first_token,"time_to_first_tool_result_s":first_result,"success":bool(done.get('success')),"infrastructure_error":error,"tool_requests":len(calls),"tool_results":len(results),"tool_failures":len(failures),"tokens_used":budget.get('tokens_used'),"cache_hits":budget.get('cache_hits'),"phase":budget.get('phase'),"required_sections":{h:h in report for h in HEADINGS},"factual_accuracy":"pending independent evidence review","errors":[e.get('message') for e in events if e.get('type') in ('error','budget_stop')]}
   (root/f'{repeat+1}-{name}.json').write_text(json.dumps({"metrics":row,"events":events},indent=2),encoding='utf-8');(root/f'{repeat+1}-{name}.md').write_text(report,encoding='utf-8')
   if any('Internal Server Error' in str(message) or 'status code: 500' in str(message) for message in row['errors']):
    row['infrastructure_error']='model_provider_server_error'
    infrastructure_streak+=1
   meta['runs'].append(row);(root/'results.json').write_text(json.dumps(meta,indent=2),encoding='utf-8')
   print(json.dumps(row),flush=True)
   if infrastructure_streak>=3:print('Stopped after three infrastructure failures',flush=True);return
 durations=[r['duration_s'] for r in meta['runs']];n=sum(r['tool_results'] for r in meta['runs']);f=sum(r['tool_failures'] for r in meta['runs'])
 meta['aggregate']={"runs":len(durations),"successful":sum(r['success'] for r in meta['runs']),"median_duration_s":statistics.median(durations),"p95_duration_s":sorted(durations)[max(0,int(len(durations)*.95)-1)],"maximum_duration_s":max(durations),"tool_failure_numerator":f,"tool_failure_denominator":n,"tool_failure_rate":f/n if n else None}
 (root/'results.json').write_text(json.dumps(meta,indent=2),encoding='utf-8');print('RESULTS:',root,flush=True)
if __name__=='__main__':main()
