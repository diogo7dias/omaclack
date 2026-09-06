//! In-memory typing stats for the panel. Dies with the process.

use serde_json::{json, Value};
use std::collections::{HashMap, VecDeque};

const WINDOW_S: f64 = 300.0;

pub struct Stats {
    times: VecDeque<f64>,
    counts: HashMap<u16, u64>,
    total: u64,
}

impl Stats {
    pub fn new() -> Self { Stats { times: VecDeque::new(), counts: HashMap::new(), total: 0 } }

    pub fn press(&mut self, code: u16, now: f64) {
        self.times.push_back(now);
        *self.counts.entry(code).or_insert(0) += 1;
        self.total += 1;
        while let Some(&t) = self.times.front() {
            if t < now - WINDOW_S { self.times.pop_front(); } else { break; }
        }
    }

    pub fn report(&self, now: f64) -> Value {
        let recent = self.times.iter().filter(|&&t| t >= now - 60.0).count();
        let mut buckets = [0u64; 10];
        for w in self.times.iter().zip(self.times.iter().skip(1)) {
            let ms = (w.1 - w.0) * 1000.0;
            let b = ((ms / 60.0).floor() as usize).min(9);
            buckets[b] += 1;
        }
        let mut top: Vec<(&u16, &u64)> = self.counts.iter().collect();
        top.sort_by(|a, b| b.1.cmp(a.1).then(a.0.cmp(b.0)));
        json!({
            "ok": true, "kpm": recent, "total": self.total, "window": self.times.len(),
            "rhythm": buckets, "top": top.iter().take(5).map(|(c, n)| json!([c, n])).collect::<Vec<_>>(),
        })
    }
}
