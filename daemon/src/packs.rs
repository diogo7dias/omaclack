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
    pub pan: bool,
}

/// -1 (left) .. +1 (right) for a key on an ANSI board. Matches tools/build_sounds.py.
pub fn key_pan(code: u16) -> f32 {
    const ROWS: &[&[u16]] = &[
        &[1, 59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 87, 88],
        &[41, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14],
        &[15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 43],
        &[58, 30, 31, 32, 33, 34, 35, 36, 37, 38, 39, 40, 28],
        &[42, 44, 45, 46, 47, 48, 49, 50, 51, 52, 53, 54],
        &[29, 125, 56, 57, 100, 126, 127, 97],
    ];
    for row in ROWS {
        if let Some(i) = row.iter().position(|&c| c == code) {
            return (i as f32 / (row.len() - 1).max(1) as f32) * 2.0 - 1.0;
        }
    }
    if (71..=83).contains(&code) || matches!(code, 55 | 69 | 74 | 78 | 96 | 98) { return 0.9; }
    if matches!(code, 99 | 70 | 119 | 102 | 103 | 104 | 105 | 106 | 107 | 108 | 109 | 110 | 111) { return 0.7; }
    0.0
}

const PAN_DEPTH: f32 = 0.35;

impl Pack {
    pub fn load(name: &str, roots: &[PathBuf]) -> Result<Self, String> {
        let dir = find_pack_dir(name, roots)
            .ok_or_else(|| format!("{}", roots[0].join(name).join("default.opus").display()))?;
        let meta = read_json(&dir.join("pack.json"));
        let keys = meta.get("keys").cloned().unwrap_or(Value::Null);
        let pan = meta.get("pan").and_then(|v| v.as_bool()).unwrap_or(false);
        let press = SampleSet::load(&dir, &keys);
        let up = SampleSet::load(&dir.join("up"), &keys);
        let release = if up.default.is_some() { Some(up) } else { None };
        Ok(Pack { name: name.to_string(), press, release, pan })
    }

    pub fn sample_for(&self, code: u16, down: bool) -> Option<&PathBuf> {
        if down { self.press.sample_for(code) } else { self.release.as_ref().and_then(|r| r.sample_for(code)) }
    }

    pub fn pan_for(&self, code: u16) -> f32 {
        if self.pan { key_pan(code) * PAN_DEPTH } else { 0.0 }
    }

    pub fn all_files(&self) -> Vec<PathBuf> {
        let mut v = self.press.all_files();
        if let Some(r) = &self.release { v.extend(r.all_files()); }
        v
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn write(path: &Path, bytes: &[u8]) { fs::write(path, bytes).unwrap(); }

    #[test]
    fn lookup_order_explicit_file_then_keys_map_then_default() {
        let tmp = tempfile();
        let shipped = tmp.join("shipped");
        let user = tmp.join("user");
        let d = shipped.join("p");
        fs::create_dir_all(d.join("up")).unwrap();
        write(&d.join("default.opus"), b"x");
        write(&d.join("30.opus"), b"x");
        write(&d.join("row1.opus"), b"x");
        write(&d.join("up").join("default.opus"), b"x");
        write(&d.join("up").join("30.opus"), b"x");
        write(&d.join("pack.json"), br#"{"name":"Pack P","keys":{"31":"row1.opus","30":"row1.opus","99":"missing.opus"}}"#);
        fs::create_dir_all(user.join("mine")).unwrap();
        write(&user.join("mine").join("default.wav"), b"x");
        let roots = vec![shipped, user];
        let p = Pack::load("p", &roots).unwrap();
        assert!(p.sample_for(30, true).unwrap().ends_with("30.opus"));
        assert!(p.sample_for(31, true).unwrap().ends_with("row1.opus"));
        assert!(p.sample_for(99, true).unwrap().ends_with("default.opus"));
        assert!(p.sample_for(30, false).unwrap().to_string_lossy().ends_with("up/30.opus"));
        assert_eq!(list_packs(&roots), vec!["mine", "p"]);
        assert!(!p.pan);
    }

    #[test]
    fn pan_flag_scales_key_position() {
        // caps row: [58, 30, 31, ...] A=30 is index 1 of 13 -> (1/12)*2-1
        let a = key_pan(30);
        assert!((a - (1.0 / 12.0 * 2.0 - 1.0)).abs() < 1e-5);
        assert!((key_pan(1) + 1.0).abs() < 1e-5); // esc, first of function row
        assert!(key_pan(272).abs() < 1e-5); // mouse, unmapped -> 0
    }

    fn tempfile() -> PathBuf {
        let p = std::env::temp_dir().join(format!("omaclack-pack-{}", std::process::id()));
        let _ = fs::remove_dir_all(&p);
        fs::create_dir_all(&p).unwrap();
        p
    }
}
