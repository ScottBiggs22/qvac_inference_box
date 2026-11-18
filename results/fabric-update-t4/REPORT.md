#### new-cram0-a / A_cold_short  (prompt=7000 +512 out, cache=cold)

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 25.2 | 25.2 | 18.3 | 19.1 | 7735 | 7744 | 7745 | 40.0 | 40.8 | 7622 | 0.1% | - | 0 |
| 5 | 1 | 6.1 | 5.3 | 25.9 | 27.1 | 15814 | 33101 | 38425 | 116.9 | 142.3 | 9121 | 0.1% | - | 0 |
| 8 | 1 | 4.7 | 4.5 | 32.8 | 34.5 | 16269 | 49984 | 61478 | 122.5 | 129.6 | 9310 | 0.1% | - | 0 |

_prefill/cache source: timings; prompt tokens actually sent: [7000]_

#### new-cram0-a / A_warm_short  (prompt=7000 +512 out, cache=warm)

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 25.9 | 25.8 | 25.7 | 25.7 | 188 | 190 | 190 | 38.8 | 39.9 | 71 | 99.8% | - | 0 |
| 5 | 1 | 11.7 | 11.7 | 57.9 | 57.9 | 513 | 589 | 608 | 85.6 | 89.1 | 174 | 99.8% | - | 0 |
| 8 | 1 | 8.1 | 8.1 | 63.9 | 63.9 | 810 | 837 | 843 | 124.2 | 128.6 | 398 | 99.8% | - | 0 |

_prefill/cache source: timings; prompt tokens actually sent: [7000]_

#### new-cram0-a / smoke  (prompt=7000 +512 out, cache=warm)

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 26.2 | 26.2 | 25.9 | 26.2 | 191 | 191 | 191 | 38.5 | 39.7 | 69 | 99.8% | - | 0 |

_prefill/cache source: timings; prompt tokens actually sent: [7000]_

#### old-cram0-a / A_cold_short  (prompt=7000 +512 out, cache=cold)

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 24.9 | 24.9 | 18.2 | 19.0 | 7607 | 7646 | 7651 | 40.2 | 41.2 | 7487 | 0.1% | - | 0 |
| 5 | 1 | 5.5 | 5.0 | 23.8 | 24.7 | 13824 | 32665 | 37936 | 145.5 | 155.5 | 9105 | 0.1% | - | 0 |
| 8 | 1 | 4.4 | 4.2 | 31.0 | 32.5 | 16141 | 49371 | 60734 | 139.8 | 144.2 | 9260 | 0.1% | - | 0 |

_prefill/cache source: timings; prompt tokens actually sent: [7000]_

#### old-cram0-a / A_warm_short  (prompt=7000 +512 out, cache=warm)

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 22.6 | 22.2 | 22.8 | 22.8 | 187 | 194 | 194 | 44.1 | 46.9 | 66 | 99.8% | - | 0 |
| 5 | 1 | 10.6 | 10.4 | 52.5 | 52.5 | 538 | 590 | 599 | 94.5 | 99.7 | 161 | 99.8% | - | 0 |
| 8 | 1 | 7.5 | 7.5 | 59.4 | 59.4 | 823 | 957 | 1003 | 133.3 | 138.6 | 400 | 99.8% | - | 0 |

_prefill/cache source: timings; prompt tokens actually sent: [7000]_

#### old-cram0-a / smoke  (prompt=7000 +512 out, cache=warm)

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 28.8 | 28.8 | 28.5 | 28.8 | 183 | 183 | 183 | 35.1 | 37.1 | 64 | 99.8% | - | 0 |

_prefill/cache source: timings; prompt tokens actually sent: [7000]_

