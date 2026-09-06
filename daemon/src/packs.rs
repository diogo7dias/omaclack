//! Sound packs on disk: <code>.<ext> wins, then the pack.json keys map, then
//! default.<ext>; release samples live in up/.

use serde_json::{json, Value};
use std::collections::HashMap;
use std::path::{Path, PathBuf};

const SAMPLE_EXT: [&str; 4] = ["opus", "ogg", "flac", "wav"];

fn default_file(dir: &Path) -> Option<PathBuf> {
    SAMPLE_EXT.iter().map(|e| dir.join(format!("default.{}", e))).find(|p| p.is_file())
}

pub fn find_pack_dir(name: &str, roots: &[PathBuf]) -> Option<PathBuf> {
    roots.iter().map(|r| r.join(name)).find(|d| default_file(d).is_some())
}

/// Pack ids across the shipped and user roots, sorted; shipped wins on collision.
pub fn list_packs(roots: &[PathBuf]) -> Vec<String> {
    let mut seen: Vec<String> = Vec::new();
    for root in roots {
        let rd = match std::fs::read_dir(root) { Ok(r) => r, Err(_) => continue };
        for e in rd.flatten() {
            let n = e.file_name().to_string_lossy().to_string();
            if !seen.contains(&n) && default_file(&e.path()).is_some() { seen.push(n); }
        }
    }
    seen.sort();
    seen
}

pub fn first_or_none(names: &[String], preferred: &str) -> Option<String> {
    if names.iter().any(|n| n == preferred) { Some(preferred.to_string()) } else { names.first().cloned() }
}

fn read_json(path: &Path) -> Value {
    std::fs::read_to_string(path).ok().and_then(|s| serde_json::from_str(&s).ok()).unwrap_or(Value::Null)
}

pub fn pack_meta(name: &str, roots: &[PathBuf]) -> Value {
    let dir = find_pack_dir(name, roots);
    let meta = dir.as_ref().map(|d| read_json(&d.join("pack.json"))).unwrap_or(Value::Null);
    let s = |k: &str| meta.get(k).and_then(|v| v.as_str()).unwrap_or("").to_string();
    let display = { let n = s("name"); if n.is_empty() { name.to_string() } else { n } };
    let user = dir.as_ref().map(|d| !d.starts_with(&roots[0])).unwrap_or(false);
    json!({"id": name, "name": display, "credit": s("credit"), "source": s("source"), "release": s("release"), "user": user})
}

pub struct SampleSet {
    default: Option<PathBuf>,
    files: HashMap<u16, PathBuf>,
}

impl SampleSet {
    fn load(dir: &Path, keys: &Value) -> Self {
        let default = default_file(dir);
        let mut files = HashMap::new();
        if default.is_some() {
            if let Ok(rd) = std::fs::read_dir(dir) {
                for e in rd.flatten() {
                    let p = e.path();
                    let ext = p.extension().and_then(|x| x.to_str()).unwrap_or("");
                    let stem = p.file_stem().and_then(|x| x.to_str()).unwrap_or("");
                    if SAMPLE_EXT.contains(&ext) {
                        if let Ok(code) = stem.parse::<u16>() { files.insert(code, p.clone()); }
                    }
                }
            }
            if let Some(map) = keys.as_object() {
                for (code, fn_) in map {
                    if let (Ok(c), Some(f)) = (code.parse::<u16>(), fn_.as_str()) {
                        let base = Path::new(f).file_name().map(|b| dir.join(b));
                        if let Some(b) = base { if !files.contains_key(&c) && b.is_file() { files.insert(c, b); } }
                    }
                }
            }
        }
        SampleSet { default, files }
    }

    pub fn sample_for(&self, code: u16) -> Option<&PathBuf> { self.files.get(&code).or(self.default.as_ref()) }

    pub fn all_files(&self) -> Vec<PathBuf> {
        let mut v: Vec<PathBuf> = self.files.values().cloned().collect();
        if let Some(d) = &self.default { v.push(d.clone()); }
        v
    }
}

pub struct Pack {
    pub name: String,
    pub press: SampleSet,
    pub release: Option<SampleSet>,
}

impl Pack {
    pub fn load(name: &str, roots: &[PathBuf]) -> Result<Self, String> {
        let dir = find_pack_dir(name, roots)
            .ok_or_else(|| format!("{}", roots[0].join(name).join("default.opus").display()))?;
        let keys = read_json(&dir.join("pack.json")).get("keys").cloned().unwrap_or(Value::Null);
        let press = SampleSet::load(&dir, &keys);
        let up = SampleSet::load(&dir.join("up"), &keys);
        let release = if up.default.is_some() { Some(up) } else { None };
        Ok(Pack { name: name.to_string(), press, release })
    }

    pub fn sample_for(&self, code: u16, down: bool) -> Option<&PathBuf> {
        if down { self.press.sample_for(code) } else { self.release.as_ref().and_then(|r| r.sample_for(code)) }
    }

    pub fn all_files(&self) -> Vec<PathBuf> {
        let mut v = self.press.all_files();
        if let Some(r) = &self.release { v.extend(r.all_files()); }
        v
    }
}
