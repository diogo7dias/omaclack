//! In-memory typing stats for the panel. Dies with the process.

use serde_json::{json, Value};
use std::collections::{HashMap, VecDeque};

const WINDOW_S: f64 = 300.0;

pub struct Stats {
    times: VecDeque<(f64, u16)>, // (t, code) last 5 min; counts follow the window
    counts: HashMap<u16, u64>,
    total: u64,                  // session total, no codes
}

impl Stats {
    pub fn new() -> Self { Stats { times: VecDeque::new(), counts: HashMap::new(), total: 0 } }

    pub fn press(&mut self, code: u16, now: f64) {
        self.times.push_back((now, code));
        *self.counts.entry(code).or_insert(0) += 1;
        self.total += 1;
        while let Some(&(t, c)) = self.times.front() {
            if t < now - WINDOW_S {
                self.times.pop_front();
                if let Some(n) = self.counts.get_mut(&c) {
                    *n -= 1;
                    if *n == 0 { self.counts.remove(&c); }
                }
            } else { break; }
        }
    }

    pub fn report(&self, now: f64) -> Value {
        let recent = self.times.iter().filter(|(t, _)| *t >= now - 60.0).count();
        let mut buckets = [0u64; 10];
        for w in self.times.iter().zip(self.times.iter().skip(1)) {
            let ms = (w.1.0 - w.0.0) * 1000.0;
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn kpm_is_presses_in_last_minute() {
        let mut s = Stats::new();
        s.press(30, 0.0);
        s.press(31, 10.0);
        s.press(32, 70.0);
        let r = s.report(70.0);
        assert_eq!(r["kpm"], 2); // 10s and 70s, not 0s
        assert_eq!(r["total"], 3);
        assert_eq!(r["window"], 3);
    }

    #[test]
    fn rhythm_buckets_are_60ms_and_top_breaks_ties_by_keycode() {
        let mut s = Stats::new();
        s.press(32, 0.00);
        s.press(30, 0.05);  // 50 ms -> bucket 0
        s.press(30, 0.20);  // 150 ms -> bucket 2
        let r = s.report(0.20);
        let rhythm = r["rhythm"].as_array().unwrap();
        assert_eq!(rhythm[0], 1);
        assert_eq!(rhythm[2], 1);
        let top = r["top"].as_array().unwrap();
        assert_eq!(top[0], json!([30, 2]));
        assert_eq!(top[1], json!([32, 1]));
    }

    #[test]
    fn drops_presses_older_than_five_minutes() {
        let mut s = Stats::new();
        s.press(30, 0.0);
        s.press(31, 301.0);
        let r = s.report(301.0);
        assert_eq!(r["window"], 1);
        assert_eq!(r["total"], 2); // session count, no codes
        let top = r["top"].as_array().unwrap();
        assert_eq!(top.len(), 1);
        assert_eq!(top[0], json!([31, 1]));
    }
}
