#### new-cram0-a / A_cold_short  (prompt=7000 +512 out, cache=cold)

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 71.1 | 71.1 | 52.1 | 54.5 | 2625 | 2635 | 2637 | 14.1 | 14.2 | 2517 | 0.1% | - | 0 |
| 5 | 1 | 19.9 | 17.8 | 82.6 | 86.6 | 5564 | 12214 | 12928 | 39.8 | 40.8 | 3122 | 0.1% | - | 0 |
| 8 | 1 | 16.8 | 15.6 | 109.3 | 115.3 | 6972 | 16632 | 20402 | 32.7 | 35.0 | 3183 | 0.1% | - | 0 |

_prefill/cache source: timings; prompt tokens actually sent: [7000]_

#### new-cram0-a / A_warm_short  (prompt=7000 +512 out, cache=warm)

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 71.1 | 71.1 | 70.3 | 70.4 | 88 | 90 | 91 | 14.1 | 14.2 | 22 | 99.8% | - | 0 |
| 5 | 1 | 39.2 | 39.1 | 192.5 | 192.9 | 242 | 281 | 286 | 25.7 | 26.0 | 71 | 99.8% | - | 0 |
| 8 | 1 | 30.9 | 30.7 | 242.4 | 242.9 | 313 | 350 | 358 | 32.5 | 34.1 | 113 | 99.8% | - | 0 |

_prefill/cache source: timings; prompt tokens actually sent: [7000]_

#### new-cram0-a / smoke  (prompt=7000 +512 out, cache=warm)

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 71.0 | 71.0 | 70.2 | 71.0 | 93 | 93 | 93 | 14.1 | 14.3 | 25 | 99.8% | - | 0 |

_prefill/cache source: timings; prompt tokens actually sent: [7000]_

#### new-cram0-b / A_cold_short  (prompt=7000 +512 out, cache=cold)

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 71.0 | 70.9 | 52.0 | 54.4 | 2632 | 2641 | 2642 | 14.2 | 14.2 | 2521 | 0.1% | - | 0 |
| 5 | 1 | 16.7 | 14.6 | 74.7 | 78.0 | 5189 | 11391 | 12993 | 51.5 | 53.0 | 3117 | 0.1% | - | 0 |
| 8 | 1 | 16.6 | 15.5 | 108.8 | 115.0 | 6858 | 16690 | 20467 | 32.9 | 35.6 | 3187 | 0.1% | - | 0 |

_prefill/cache source: timings; prompt tokens actually sent: [7000]_

#### new-cram0-b / A_warm_short  (prompt=7000 +512 out, cache=warm)

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 71.1 | 71.0 | 70.2 | 70.3 | 91 | 117 | 124 | 14.1 | 14.2 | 23 | 99.8% | - | 0 |
| 5 | 1 | 38.9 | 38.0 | 190.3 | 190.6 | 217 | 245 | 257 | 25.9 | 26.7 | 61 | 99.8% | - | 0 |
| 8 | 1 | 30.7 | 30.6 | 241.3 | 241.7 | 310 | 375 | 381 | 32.6 | 34.2 | 100 | 99.8% | - | 0 |

_prefill/cache source: timings; prompt tokens actually sent: [7000]_

#### new-cram0-nofusion / A_warm_short  (prompt=7000 +512 out, cache=warm)

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 71.2 | 71.1 | 70.4 | 70.4 | 88 | 92 | 93 | 14.1 | 14.2 | 21 | 99.8% | - | 0 |
| 5 | 1 | 39.1 | 39.0 | 192.4 | 192.7 | 226 | 255 | 264 | 25.7 | 26.1 | 54 | 99.8% | - | 0 |
| 8 | 1 | 30.8 | 30.2 | 241.6 | 242.0 | 286 | 323 | 334 | 32.5 | 34.6 | 86 | 99.8% | - | 0 |

_prefill/cache source: timings; prompt tokens actually sent: [7000]_

#### new-default / A_warm_short  (prompt=7000 +512 out, cache=warm)

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 71.1 | 71.0 | 70.3 | 70.3 | 91 | 97 | 99 | 14.1 | 14.2 | 23 | 99.8% | - | 0 |
| 5 | 1 | 39.2 | 39.1 | 164.7 | 168.7 | 2524 | 2571 | 2575 | 25.7 | 26.0 | 83 | 99.8% | - | 0 |
| 8 | 1 | 30.8 | 30.6 | 195.6 | 201.9 | 4334 | 4437 | 4449 | 32.6 | 34.2 | 118 | 99.8% | - | 0 |

_prefill/cache source: timings; prompt tokens actually sent: [7000]_

#### old-cram0-a / A_cold_short  (prompt=7000 +512 out, cache=cold)

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 69.8 | 69.8 | 51.3 | 53.6 | 2649 | 2652 | 2652 | 14.4 | 14.5 | 2531 | 0.1% | - | 0 |
| 5 | 1 | 18.1 | 13.9 | 70.7 | 73.2 | 5882 | 12512 | 13106 | 43.5 | 54.7 | 3169 | 0.1% | - | 0 |
| 8 | 1 | 12.7 | 12.2 | 88.0 | 92.0 | 6439 | 16887 | 20773 | 50.3 | 52.1 | 3252 | 0.1% | - | 0 |

_prefill/cache source: timings; prompt tokens actually sent: [7000]_

#### old-cram0-a / A_warm_short  (prompt=7000 +512 out, cache=warm)

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 70.0 | 69.7 | 68.8 | 68.9 | 140 | 143 | 144 | 14.3 | 14.5 | 23 | 99.8% | - | 0 |
| 5 | 1 | 29.7 | 29.3 | 146.0 | 146.2 | 258 | 299 | 303 | 33.9 | 35.0 | 66 | 99.8% | - | 0 |
| 8 | 1 | 20.1 | 20.1 | 159.0 | 159.1 | 354 | 408 | 437 | 49.8 | 50.9 | 111 | 99.8% | - | 0 |

_prefill/cache source: timings; prompt tokens actually sent: [7000]_

#### old-cram0-a / smoke  (prompt=7000 +512 out, cache=warm)

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 70.6 | 70.6 | 69.3 | 70.5 | 138 | 138 | 138 | 14.2 | 14.4 | 23 | 99.8% | - | 0 |

_prefill/cache source: timings; prompt tokens actually sent: [7000]_

#### old-cram0-b / A_cold_short  (prompt=7000 +512 out, cache=cold)

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 69.7 | 69.7 | 51.3 | 53.5 | 2656 | 2659 | 2659 | 14.4 | 14.5 | 2538 | 0.1% | - | 0 |
| 5 | 1 | 18.2 | 16.6 | 76.0 | 79.4 | 5858 | 12815 | 13455 | 43.3 | 45.1 | 3159 | 0.1% | - | 0 |
| 8 | 1 | 12.7 | 12.2 | 88.0 | 92.1 | 6396 | 16876 | 20769 | 50.3 | 52.3 | 3246 | 0.1% | - | 0 |

_prefill/cache source: timings; prompt tokens actually sent: [7000]_

#### old-cram0-b / A_warm_short  (prompt=7000 +512 out, cache=warm)

| conc | reps | tok/s/user p50 | p05 | aggr wall | aggr steady | TTFT p50 | p95 | p99 | ITL p50 | ITL p95 | prefill ms | cache hit | queue ms | fail |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 69.6 | 69.6 | 68.4 | 68.5 | 140 | 141 | 141 | 14.4 | 14.5 | 23 | 99.8% | - | 0 |
| 5 | 1 | 29.9 | 29.9 | 147.7 | 147.8 | 259 | 296 | 302 | 33.5 | 34.6 | 60 | 99.8% | - | 0 |
| 8 | 1 | 20.1 | 20.0 | 158.7 | 158.9 | 361 | 438 | 451 | 49.8 | 50.9 | 116 | 99.8% | - | 0 |

_prefill/cache source: timings; prompt tokens actually sent: [7000]_

