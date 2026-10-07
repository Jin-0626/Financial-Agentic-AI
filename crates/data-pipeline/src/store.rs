use crate::{valid_ticker, Dataset, PipelineError, Snapshot};
use polars::prelude::*;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, File},
    io::{Read, Write},
    path::{Path, PathBuf},
};
use tempfile::NamedTempFile;

const MAX_RECORD: u64 = 8 * 1024 * 1024;
pub fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
pub fn valid_id(id: &str) -> bool {
    id.len() == 64
        && id
            .bytes()
            .all(|c| c.is_ascii_digit() || (b'a'..=b'f').contains(&c))
}
#[derive(Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Record {
    snapshot: Snapshot,
    parquet_hash: Option<String>,
}
#[derive(Debug, Clone)]
pub struct SnapshotStore {
    root: PathBuf,
    org_id: String,
}
fn check_directory(path: &Path) -> Result<(), PipelineError> {
    let metadata = fs::symlink_metadata(path).map_err(|_| PipelineError::Storage)?;
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        return Err(PipelineError::Storage);
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        if metadata.file_attributes() & 0x400 != 0 {
            return Err(PipelineError::Storage);
        }
    }
    Ok(())
}
fn bounded_read(path: &Path) -> Result<Vec<u8>, PipelineError> {
    let metadata = fs::symlink_metadata(path).map_err(|_| PipelineError::Unavailable)?;
    if !metadata.is_file() || metadata.file_type().is_symlink() || metadata.len() > MAX_RECORD {
        return Err(PipelineError::Corrupt);
    }
    let mut bytes = Vec::new();
    File::open(path)
        .map_err(|_| PipelineError::Storage)?
        .take(MAX_RECORD + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| PipelineError::Storage)?;
    if bytes.len() as u64 > MAX_RECORD {
        return Err(PipelineError::Corrupt);
    }
    Ok(bytes)
}
fn sync_directory(path: &Path) -> Result<(), PipelineError> {
    #[cfg(unix)]
    {
        File::open(path)
            .and_then(|file| file.sync_all())
            .map_err(|_| PipelineError::Storage)?;
    }
    #[cfg(not(unix))]
    {
        let _ = path;
    }
    Ok(())
}
fn atomic_write(
    root: &Path,
    destination: &Path,
    bytes: &[u8],
    replace: bool,
) -> Result<(), PipelineError> {
    let mut temporary = NamedTempFile::new_in(root).map_err(|_| PipelineError::Storage)?;
    temporary
        .write_all(bytes)
        .and_then(|_| temporary.as_file().sync_all())
        .map_err(|_| PipelineError::Storage)?;
    if replace {
        temporary
            .persist(destination)
            .map_err(|_| PipelineError::Storage)?;
    } else if let Err(error) = temporary.persist_noclobber(destination) {
        if error.error.kind() != std::io::ErrorKind::AlreadyExists
            || bounded_read(destination)? != bytes
        {
            return Err(PipelineError::Corrupt);
        }
    }
    sync_directory(root)
}
impl SnapshotStore {
    pub fn new(root: &Path, org_id: &str) -> Result<Self, PipelineError> {
        if org_id.is_empty() || org_id.len() > 128 {
            return Err(PipelineError::InvalidData);
        }
        fs::create_dir_all(root).map_err(|_| PipelineError::Storage)?;
        check_directory(root)?;
        let root = root.canonicalize().map_err(|_| PipelineError::Storage)?;
        let partition = root.join(digest(org_id.as_bytes()));
        fs::create_dir_all(&partition).map_err(|_| PipelineError::Storage)?;
        check_directory(&partition)?;
        Ok(Self {
            root: partition,
            org_id: org_id.into(),
        })
    }
    fn index(&self, ticker: &str, kind: &str) -> PathBuf {
        self.root.join(format!(
            "{}.index",
            digest(format!("{kind}:{ticker}").as_bytes())
        ))
    }
    pub fn import(&self, snapshot: Snapshot) -> Result<String, PipelineError> {
        let _span = tracing::info_span!("artifact.commit").entered();
        snapshot.validate()?;
        if snapshot.org_id != self.org_id {
            return Err(PipelineError::InvalidData);
        }
        let parquet = match &snapshot.dataset {
            Dataset::Prices { bars } => {
                let dates: Vec<String> = bars.iter().map(|bar| bar.date.to_string()).collect();
                let closes: Vec<f64> = bars.iter().map(|bar| bar.adjusted_close).collect();
                let mut frame = DataFrame::new(
                    bars.len(),
                    vec![
                        Series::new("date".into(), dates).into(),
                        Series::new("adjusted_close".into(), closes).into(),
                    ],
                )
                .map_err(|_| PipelineError::InvalidData)?;
                let mut buffer = std::io::Cursor::new(Vec::new());
                ParquetWriter::new(&mut buffer)
                    .finish(&mut frame)
                    .map_err(|_| PipelineError::Storage)?;
                Some(buffer.into_inner())
            }
            _ => None,
        };
        let record = Record {
            snapshot,
            parquet_hash: parquet.as_ref().map(|bytes| digest(bytes)),
        };
        let bytes = serde_json::to_vec(&record).map_err(|_| PipelineError::InvalidData)?;
        if bytes.len() as u64 > MAX_RECORD {
            return Err(PipelineError::InvalidData);
        }
        let id = digest(&bytes);
        atomic_write(
            &self.root,
            &self.root.join(format!("{id}.json")),
            &bytes,
            false,
        )?;
        if let Some(parquet) = parquet {
            atomic_write(
                &self.root,
                &self.root.join(format!("{id}.parquet")),
                &parquet,
                false,
            )?;
        }
        atomic_write(
            &self.root,
            &self.index(&record.snapshot.ticker, record.snapshot.dataset.kind()),
            id.as_bytes(),
            true,
        )?;
        Ok(id)
    }
    pub fn commit_artifact(&self, value: &serde_json::Value) -> Result<String, PipelineError> {
        let _span = tracing::info_span!("artifact.commit").entered();
        let bytes = serde_json::to_vec(value).map_err(|_| PipelineError::InvalidData)?;
        if bytes.len() as u64 > MAX_RECORD {
            return Err(PipelineError::InvalidData);
        }
        let id = digest(&bytes);
        atomic_write(
            &self.root,
            &self.root.join(format!("{id}.artifact.json")),
            &bytes,
            false,
        )?;
        Ok(id)
    }
    pub fn read_artifact(&self, id: &str) -> Result<serde_json::Value, PipelineError> {
        if !valid_id(id) {
            return Err(PipelineError::InvalidData);
        }
        let bytes = bounded_read(&self.root.join(format!("{id}.artifact.json")))?;
        if digest(&bytes) != id {
            return Err(PipelineError::Corrupt);
        }
        crate::parse_unique(&bytes).map_err(|_| PipelineError::Corrupt)
    }
    pub fn latest(&self, ticker: &str, kind: &str) -> Result<String, PipelineError> {
        if !valid_ticker(ticker) || !["prices", "statements", "forecast"].contains(&kind) {
            return Err(PipelineError::InvalidData);
        }
        let bytes = bounded_read(&self.index(ticker, kind))?;
        let id = String::from_utf8(bytes).map_err(|_| PipelineError::Corrupt)?;
        if !valid_id(&id) {
            return Err(PipelineError::Corrupt);
        }
        Ok(id)
    }
    pub fn load(&self, id: &str, ticker: &str, kind: &str) -> Result<Snapshot, PipelineError> {
        let _span = tracing::info_span!("data.fetch").entered();
        if !valid_id(id) {
            return Err(PipelineError::InvalidData);
        }
        let bytes = bounded_read(&self.root.join(format!("{id}.json")))?;
        if digest(&bytes) != id {
            return Err(PipelineError::Corrupt);
        }
        let record: Record = serde_json::from_slice(&bytes).map_err(|_| PipelineError::Corrupt)?;
        record.snapshot.validate()?;
        if record.snapshot.org_id != self.org_id
            || record.snapshot.ticker != ticker
            || record.snapshot.dataset.kind() != kind
        {
            return Err(PipelineError::InvalidData);
        }
        if let Some(hash) = record.parquet_hash {
            if digest(&bounded_read(&self.root.join(format!("{id}.parquet")))?) != hash {
                return Err(PipelineError::Corrupt);
            }
        }
        Ok(record.snapshot)
    }
    pub fn returns(
        &self,
        id: &str,
        ticker: &str,
        lookback: u32,
    ) -> Result<(Snapshot, Vec<f64>, Vec<f64>, f64), PipelineError> {
        let snapshot = self.load(id, ticker, "prices")?;
        let Dataset::Prices { bars } = &snapshot.dataset else {
            return Err(PipelineError::InvalidData);
        };
        if !(2..=10_000).contains(&lookback) || bars.len() <= lookback as usize {
            return Err(PipelineError::Unavailable);
        }
        let bytes = bounded_read(&self.root.join(format!("{id}.parquet")))?;
        let frame = ParquetReader::new(std::io::Cursor::new(bytes))
            .finish()
            .map_err(|_| PipelineError::Corrupt)?;
        let returns = frame
            .lazy()
            .select([
                ((col("adjusted_close") / col("adjusted_close").shift(lit(1))) - lit(1.0))
                    .alias("returns"),
            ])
            .slice((bars.len() - lookback as usize) as i64, lookback)
            .collect()
            .map_err(|_| PipelineError::Corrupt)?;
        let values: Vec<f64> = returns
            .column("returns")
            .map_err(|_| PipelineError::Corrupt)?
            .f64()
            .map_err(|_| PipelineError::Corrupt)?
            .iter()
            .collect::<Option<Vec<f64>>>()
            .ok_or(PipelineError::Corrupt)?;
        if values.iter().any(|x| !x.is_finite() || *x <= -1.0) {
            return Err(PipelineError::InvalidData);
        }
        let log_returns: Vec<f64> = values.iter().map(|x| x.ln_1p()).collect();
        let spot = bars
            .last()
            .ok_or(PipelineError::Unavailable)?
            .adjusted_close;
        Ok((snapshot, values, log_returns, spot))
    }
}
