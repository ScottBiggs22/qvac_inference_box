### vllm (A10) vs vllm-c8k-u085 (T4)

Peak VRAM from gpu.csv: A10 21621 MiB, T4 12479 MiB

#### A_warm  (A10 file A_warm.jsonl, T4 file A_warm_short.jsonl)

| conc | decode A10 | decode T4 | T4/A10 | aggr A10 | aggr T4 | T4/A10 | TTFT p50 A10 | T4 | p95 A10 | T4 | cache A10 | T4 | fail A10/T4 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 68.7 | 19.1 | 0.28x | 67.8 | 19.0 | 0.28x | 117 | 275 | 122 | 282 | 100% | 100% | 0/0 |
| 5 | 45.9 | 7.0 | 0.15x | 226.1 | 34.7 | 0.15x | 163 | 872 | 245 | 919 | 100% | 100% | 0/0 |
| 8 | 43.2 | 4.8 | 0.11x | 339.5 | 37.6 | 0.11x | 194 | 1374 | 314 | 1458 | 100% | 100% | 0/0 |

_reps: A10 [3], T4 [1]; levels compared: [1, 5, 8]_

#### smoke  (A10 file smoke.jsonl, T4 file smoke.jsonl)

| conc | decode A10 | decode T4 | T4/A10 | aggr A10 | aggr T4 | T4/A10 | TTFT p50 A10 | T4 | p95 A10 | T4 | cache A10 | T4 | fail A10/T4 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 69.0 | 19.6 | 0.28x | 68.0 | 19.4 | 0.29x | 115 | 269 | 115 | 269 | 100% | 100% | 0/0 |

_reps: A10 [1], T4 [1]; levels compared: [1]_

_not compared: A10-only ['A_cold', 'B_legacy', 'C', 'D_long', 'E_sat', 'F_cross', 'F_partial', 'G_gateway'], T4-only ['devops_cold', 'devops_warm']_

### vllm (A10) vs vllm-c4k-u085 (T4)

Peak VRAM from gpu.csv: A10 21621 MiB, T4 12479 MiB

#### A_cold  (A10 file A_cold.jsonl, T4 file A_cold_short.jsonl)

> SHAPE MISMATCH: prompt_tokens_target: A10 7000 vs T4 3500

| conc | decode A10 | decode T4 | T4/A10 | aggr A10 | aggr T4 | T4/A10 | TTFT p50 A10 | T4 | p95 A10 | T4 | cache A10 | T4 | fail A10/T4 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 68.5 | 24.7 | 0.36x | 54.4 | 18.2 | 0.34x | 1963 | 7299 | 1966 | 7508 | 0% | 0% | 0/0 |
| 5 | 27.8 | 7.6 | 0.27x | 111.3 | 31.4 | 0.28x | 4607 | 14249 | 7195 | 26782 | 0% | 0% | 0/0 |
| 8 | 19.6 | 4.8 | 0.25x | 132.6 | 33.9 | 0.26x | 4698 | 14364 | 11500 | 45385 | 0% | 0% | 0/0 |

_reps: A10 [3], T4 [1]; levels compared: [1, 5, 8]_

#### A_warm  (A10 file A_warm.jsonl, T4 file A_warm_short.jsonl)

> SHAPE MISMATCH: prompt_tokens_target: A10 7000 vs T4 3500

| conc | decode A10 | decode T4 | T4/A10 | aggr A10 | aggr T4 | T4/A10 | TTFT p50 A10 | T4 | p95 A10 | T4 | cache A10 | T4 | fail A10/T4 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 68.7 | 26.3 | 0.38x | 67.8 | 26.1 | 0.39x | 117 | 179 | 122 | 181 | 100% | 99% | 0/0 |
| 5 | 45.9 | 11.4 | 0.25x | 226.1 | 56.6 | 0.25x | 163 | 582 | 245 | 619 | 100% | 99% | 0/0 |
| 8 | 43.2 | 8.2 | 0.19x | 339.5 | 64.5 | 0.19x | 194 | 790 | 314 | 948 | 100% | 99% | 0/0 |

_reps: A10 [3], T4 [1]; levels compared: [1, 5, 8]_

#### smoke  (A10 file smoke.jsonl, T4 file smoke.jsonl)

> SHAPE MISMATCH: prompt_tokens_target: A10 7000 vs T4 3500

| conc | decode A10 | decode T4 | T4/A10 | aggr A10 | aggr T4 | T4/A10 | TTFT p50 A10 | T4 | p95 A10 | T4 | cache A10 | T4 | fail A10/T4 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 69.0 | 27.1 | 0.39x | 68.0 | 26.9 | 0.40x | 115 | 173 | 115 | 173 | 100% | 99% | 0/0 |

_reps: A10 [1], T4 [1]; levels compared: [1]_

_not compared: A10-only ['B_legacy', 'C', 'D_long', 'E_sat', 'F_cross', 'F_partial', 'G_gateway'], T4-only []_

### vllm (A10) vs vllm-c2k-u085 (T4)

Peak VRAM from gpu.csv: A10 21621 MiB, T4 12479 MiB

#### A_cold  (A10 file A_cold.jsonl, T4 file A_cold_short.jsonl)

> SHAPE MISMATCH: prompt_tokens_target: A10 7000 vs T4 1500

| conc | decode A10 | decode T4 | T4/A10 | aggr A10 | aggr T4 | T4/A10 | TTFT p50 A10 | T4 | p95 A10 | T4 | cache A10 | T4 | fail A10/T4 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 68.5 | 31.7 | 0.46x | 54.4 | 28.4 | 0.52x | 1963 | 1894 | 1966 | 1898 | 0% | 0% | 0/0 |
| 5 | 27.8 | 15.5 | 0.56x | 111.3 | 66.5 | 0.60x | 4607 | 5626 | 7195 | 8634 | 0% | 0% | 0/0 |
| 8 | 19.6 | 10.8 | 0.55x | 132.6 | 77.0 | 0.58x | 4698 | 5738 | 11500 | 11200 | 0% | 0% | 0/0 |

_reps: A10 [3], T4 [1]; levels compared: [1, 5, 8]_

#### A_warm  (A10 file A_warm.jsonl, T4 file A_warm_short.jsonl)

> SHAPE MISMATCH: prompt_tokens_target: A10 7000 vs T4 1500

| conc | decode A10 | decode T4 | T4/A10 | aggr A10 | aggr T4 | T4/A10 | TTFT p50 A10 | T4 | p95 A10 | T4 | cache A10 | T4 | fail A10/T4 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 68.7 | 33.0 | 0.48x | 67.8 | 32.9 | 0.49x | 117 | 103 | 122 | 109 | 100% | 98% | 0/0 |
| 5 | 45.9 | 18.0 | 0.39x | 226.1 | 89.3 | 0.39x | 163 | 341 | 245 | 348 | 100% | 98% | 0/0 |
| 8 | 43.2 | 13.8 | 0.32x | 339.5 | 109.2 | 0.32x | 194 | 446 | 314 | 526 | 100% | 98% | 0/0 |

_reps: A10 [3], T4 [1]; levels compared: [1, 5, 8]_

#### smoke  (A10 file smoke.jsonl, T4 file smoke.jsonl)

> SHAPE MISMATCH: prompt_tokens_target: A10 7000 vs T4 1500

| conc | decode A10 | decode T4 | T4/A10 | aggr A10 | aggr T4 | T4/A10 | TTFT p50 A10 | T4 | p95 A10 | T4 | cache A10 | T4 | fail A10/T4 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 69.0 | 34.3 | 0.50x | 68.0 | 34.1 | 0.50x | 115 | 97 | 115 | 97 | 100% | 98% | 0/0 |

_reps: A10 [1], T4 [1]; levels compared: [1]_

_not compared: A10-only ['B_legacy', 'C', 'D_long', 'E_sat', 'F_cross', 'F_partial', 'G_gateway'], T4-only []_

### vllm (A10) vs vllm-c1k-u085 (T4)

Peak VRAM from gpu.csv: A10 21621 MiB, T4 12479 MiB

#### A_cold  (A10 file A_cold.jsonl, T4 file A_cold_short.jsonl)

> SHAPE MISMATCH: prompt_tokens_target: A10 7000 vs T4 500

| conc | decode A10 | decode T4 | T4/A10 | aggr A10 | aggr T4 | T4/A10 | TTFT p50 A10 | T4 | p95 A10 | T4 | cache A10 | T4 | fail A10/T4 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 68.5 | 35.7 | 0.52x | 54.4 | 34.6 | 0.64x | 1963 | 453 | 1966 | 457 | 0% | 0% | 0/0 |
| 5 | 27.8 | 24.8 | 0.89x | 111.3 | 112.4 | 1.01x | 4607 | 2148 | 7195 | 2277 | 0% | 0% | 0/0 |
| 8 | 19.6 | 20.1 | 1.03x | 132.6 | 147.9 | 1.12x | 4698 | 2297 | 11500 | 3517 | 0% | 0% | 0/0 |

_reps: A10 [3], T4 [1]; levels compared: [1, 5, 8]_

#### A_warm  (A10 file A_warm.jsonl, T4 file A_warm_short.jsonl)

> SHAPE MISMATCH: prompt_tokens_target: A10 7000 vs T4 500

| conc | decode A10 | decode T4 | T4/A10 | aggr A10 | aggr T4 | T4/A10 | TTFT p50 A10 | T4 | p95 A10 | T4 | cache A10 | T4 | fail A10/T4 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 68.7 | 37.0 | 0.54x | 67.8 | 37.0 | 0.54x | 117 | 66 | 122 | 69 | 100% | 96% | 0/0 |
| 5 | 45.9 | 25.3 | 0.55x | 226.1 | 125.7 | 0.56x | 163 | 166 | 245 | 190 | 100% | 96% | 0/0 |
| 8 | 43.2 | 21.4 | 0.50x | 339.5 | 170.3 | 0.50x | 194 | 236 | 314 | 263 | 100% | 96% | 0/0 |

_reps: A10 [3], T4 [1]; levels compared: [1, 5, 8]_

#### smoke  (A10 file smoke.jsonl, T4 file smoke.jsonl)

> SHAPE MISMATCH: prompt_tokens_target: A10 7000 vs T4 500

| conc | decode A10 | decode T4 | T4/A10 | aggr A10 | aggr T4 | T4/A10 | TTFT p50 A10 | T4 | p95 A10 | T4 | cache A10 | T4 | fail A10/T4 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 69.0 | 38.3 | 0.56x | 68.0 | 38.2 | 0.56x | 115 | 60 | 115 | 60 | 100% | 96% | 0/0 |

_reps: A10 [1], T4 [1]; levels compared: [1]_

_not compared: A10-only ['B_legacy', 'C', 'D_long', 'E_sat', 'F_cross', 'F_partial', 'G_gateway'], T4-only []_

### vllm (A10) vs vllm-c8k-u090 (T4)

Peak VRAM from gpu.csv: A10 21621 MiB, T4 14279 MiB

#### A_cold  (A10 file A_cold.jsonl, T4 file A_cold_short.jsonl)

| conc | decode A10 | decode T4 | T4/A10 | aggr A10 | aggr T4 | T4/A10 | TTFT p50 A10 | T4 | p95 A10 | T4 | cache A10 | T4 | fail A10/T4 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 68.5 | 18.7 | 0.27x | 54.4 | 10.1 | 0.19x | 1963 | 23592 | 1966 | 23609 | 0% | 0% | 0/0 |
| 5 | 27.8 | 3.4 | 0.12x | 111.3 | 13.2 | 0.12x | 4607 | 42769 | 7195 | 85430 | 0% | 0% | 0/0 |
| 8 | 19.6 | 2.4 | 0.12x | 132.6 | 12.9 | 0.10x | 4698 | 83825 | 11500 | 174989 | 0% | 0% | 0/0 |

_reps: A10 [3], T4 [1]; levels compared: [1, 5, 8]_

#### A_warm  (A10 file A_warm.jsonl, T4 file A_warm_short.jsonl)

| conc | decode A10 | decode T4 | T4/A10 | aggr A10 | aggr T4 | T4/A10 | TTFT p50 A10 | T4 | p95 A10 | T4 | cache A10 | T4 | fail A10/T4 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 68.7 | 18.9 | 0.27x | 67.8 | 18.7 | 0.28x | 117 | 273 | 122 | 281 | 100% | 100% | 0/0 |
| 5 | 45.9 | 7.0 | 0.15x | 226.1 | 34.4 | 0.15x | 163 | 933 | 245 | 1017 | 100% | 100% | 0/0 |
| 8 | 43.2 | 4.7 | 0.11x | 339.5 | 37.2 | 0.11x | 194 | 1319 | 314 | 1484 | 100% | 100% | 0/0 |

_reps: A10 [3], T4 [1]; levels compared: [1, 5, 8]_

#### E_sat  (A10 file E_sat.jsonl, T4 file E_sat.jsonl)

| conc | decode A10 | decode T4 | T4/A10 | aggr A10 | aggr T4 | T4/A10 | TTFT p50 A10 | T4 | p95 A10 | T4 | cache A10 | T4 | fail A10/T4 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 12 | 32.5 | 3.3 | 0.10x | 383.2 | 38.8 | 0.10x | 320 | 1682 | 430 | 2267 | 100% | 100% | 0/0 |
| 16 | 30.9 | 2.5 | 0.08x | 483.1 | 39.9 | 0.08x | 352 | 1794 | 490 | 2910 | 100% | 100% | 0/0 |

_reps: A10 [1], T4 [1]; levels compared: [12, 16]_

#### F_cross  (A10 file F_cross.jsonl, T4 file F_cross.jsonl)

| conc | decode A10 | decode T4 | T4/A10 | aggr A10 | aggr T4 | T4/A10 | TTFT p50 A10 | T4 | p95 A10 | T4 | cache A10 | T4 | fail A10/T4 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2 | 61.5 | 13.2 | 0.21x | 120.9 | 26.1 | 0.22x | 156 | 399 | 160 | 504 | 100% | 100% | 0/0 |
| 4 | 54.9 | 8.3 | 0.15x | 214.6 | 32.8 | 0.15x | 216 | 661 | 229 | 852 | 100% | 100% | 0/0 |
| 8 | 43.4 | 4.7 | 0.11x | 338.1 | 37.5 | 0.11x | 300 | 1488 | 351 | 1495 | 100% | 100% | 0/0 |

_reps: A10 [3], T4 [3]; levels compared: [2, 4, 8]_

#### smoke  (A10 file smoke.jsonl, T4 file smoke.jsonl)

| conc | decode A10 | decode T4 | T4/A10 | aggr A10 | aggr T4 | T4/A10 | TTFT p50 A10 | T4 | p95 A10 | T4 | cache A10 | T4 | fail A10/T4 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 69.0 | 19.2 | 0.28x | 68.0 | 19.1 | 0.28x | 115 | 251 | 115 | 251 | 100% | 100% | 0/0 |

_reps: A10 [1], T4 [1]; levels compared: [1]_

_not compared: A10-only ['B_legacy', 'C', 'D_long', 'F_partial', 'G_gateway'], T4-only []_

### qvac-q4km-cram0 (A10) vs qvac-q4km-cram0-c8k-np8 (T4)

Peak VRAM from gpu.csv: A10 14063 MiB, T4 13913 MiB

#### A_cold  (A10 file A_cold_r2.jsonl, T4 file A_cold_short.jsonl)

| conc | decode A10 | decode T4 | T4/A10 | aggr A10 | aggr T4 | T4/A10 | TTFT p50 A10 | T4 | p95 A10 | T4 | cache A10 | T4 | fail A10/T4 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 69.7 | 26.4 | 0.38x | 51.4 | 18.8 | 0.37x | 2619 | 7796 | 2621 | 7803 | 0% | 0% | 0/0 |
| 5 | 15.7 | 6.6 | 0.42x | 68.9 | 26.2 | 0.38x | 4506 | 15743 | 10756 | 34568 | 0% | 0% | 0/0 |
| 8 | 12.3 | 4.6 | 0.38x | 88.3 | 32.2 | 0.36x | 4836 | 16339 | 15341 | 52407 | 0% | 0% | 0/0 |

_reps: A10 [2], T4 [1]; levels compared: [1, 5, 8]_

#### A_warm  (A10 file A_warm_r2.jsonl, T4 file A_warm_short.jsonl)

| conc | decode A10 | decode T4 | T4/A10 | aggr A10 | aggr T4 | T4/A10 | TTFT p50 A10 | T4 | p95 A10 | T4 | cache A10 | T4 | fail A10/T4 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 69.6 | 32.1 | 0.46x | 68.9 | 31.7 | 0.46x | 88 | 201 | 94 | 203 | 100% | 100% | 0/0 |
| 5 | 26.5 | 11.6 | 0.44x | 132.8 | 57.9 | 0.44x | 182 | 515 | 214 | 603 | 100% | 100% | 0/0 |
| 8 | 20.0 | 7.9 | 0.39x | 159.1 | 62.5 | 0.39x | 225 | 794 | 294 | 896 | 100% | 100% | 0/0 |

_reps: A10 [2], T4 [1]; levels compared: [1, 5, 8]_

#### F_cross  (A10 file F_cross.jsonl, T4 file F_cross.jsonl)

| conc | decode A10 | decode T4 | T4/A10 | aggr A10 | aggr T4 | T4/A10 | TTFT p50 A10 | T4 | p95 A10 | T4 | cache A10 | T4 | fail A10/T4 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2 | 42.8 | 15.2 | 0.35x | 75.5 | 25.2 | 0.33x | 1388 | 6770 | 2555 | 7857 | 50% | 50% | 0/0 |
| 4 | 28.3 | 11.3 | 0.40x | 90.7 | 32.6 | 0.36x | 4385 | 17029 | 7629 | 23344 | 25% | 25% | 0/0 |
| 8 | 15.1 | 6.0 | 0.40x | 93.0 | 34.2 | 0.37x | 9777 | 33437 | 18084 | 55342 | 13% | 13% | 0/0 |

_reps: A10 [3], T4 [3]; levels compared: [2, 4, 8]_

_not compared: A10-only ['F_partial'], T4-only ['E_sat', 'smoke']_

### qvac-q4km-cram0 (A10) vs qvac-q4km-cram0-c4k-np8 (T4)

Peak VRAM from gpu.csv: A10 14063 MiB, T4 9301 MiB

#### A_cold  (A10 file A_cold_r2.jsonl, T4 file A_cold_short.jsonl)

> SHAPE MISMATCH: prompt_tokens_target: A10 7000 vs T4 3500

| conc | decode A10 | decode T4 | T4/A10 | aggr A10 | aggr T4 | T4/A10 | TTFT p50 A10 | T4 | p95 A10 | T4 | cache A10 | T4 | fail A10/T4 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 69.7 | 28.0 | 0.40x | 51.4 | 23.4 | 0.45x | 2619 | 3672 | 2621 | 3678 | 0% | 0% | 0/0 |
| 5 | 15.7 | 8.2 | 0.52x | 68.9 | 35.9 | 0.52x | 4506 | 9106 | 10756 | 17811 | 0% | 0% | 0/0 |
| 8 | 12.3 | 6.7 | 0.54x | 88.3 | 47.6 | 0.54x | 4836 | 9336 | 15341 | 26474 | 0% | 0% | 0/0 |

_reps: A10 [2], T4 [1]; levels compared: [1, 5, 8]_

#### A_warm  (A10 file A_warm_r2.jsonl, T4 file A_warm_short.jsonl)

> SHAPE MISMATCH: prompt_tokens_target: A10 7000 vs T4 3500

| conc | decode A10 | decode T4 | T4/A10 | aggr A10 | aggr T4 | T4/A10 | TTFT p50 A10 | T4 | p95 A10 | T4 | cache A10 | T4 | fail A10/T4 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 69.6 | 28.1 | 0.40x | 68.9 | 27.9 | 0.40x | 88 | 174 | 94 | 176 | 100% | 100% | 0/0 |
| 5 | 26.5 | 12.4 | 0.47x | 132.8 | 61.5 | 0.46x | 182 | 479 | 214 | 516 | 100% | 100% | 0/0 |
| 8 | 20.0 | 9.0 | 0.45x | 159.1 | 71.2 | 0.45x | 225 | 693 | 294 | 761 | 100% | 100% | 0/0 |

_reps: A10 [2], T4 [1]; levels compared: [1, 5, 8]_

_not compared: A10-only ['F_cross', 'F_partial'], T4-only ['smoke']_

### qvac-q4km-cram0 (A10) vs qvac-q4km-cram0-c2k-np8 (T4)

Peak VRAM from gpu.csv: A10 14063 MiB, T4 6995 MiB

#### A_cold  (A10 file A_cold_r2.jsonl, T4 file A_cold_short.jsonl)

> SHAPE MISMATCH: prompt_tokens_target: A10 7000 vs T4 1500

| conc | decode A10 | decode T4 | T4/A10 | aggr A10 | aggr T4 | T4/A10 | TTFT p50 A10 | T4 | p95 A10 | T4 | cache A10 | T4 | fail A10/T4 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 69.7 | 30.1 | 0.43x | 51.4 | 27.6 | 0.54x | 2619 | 1548 | 2621 | 1552 | 0% | 0% | 0/0 |
| 5 | 15.7 | 9.9 | 0.63x | 68.9 | 45.3 | 0.66x | 4506 | 5190 | 10756 | 7635 | 0% | 0% | 0/0 |
| 8 | 12.3 | 8.9 | 0.73x | 88.3 | 64.4 | 0.73x | 4836 | 6289 | 15341 | 12261 | 0% | 0% | 0/0 |

_reps: A10 [2], T4 [1]; levels compared: [1, 5, 8]_

#### A_warm  (A10 file A_warm_r2.jsonl, T4 file A_warm_short.jsonl)

> SHAPE MISMATCH: prompt_tokens_target: A10 7000 vs T4 1500

| conc | decode A10 | decode T4 | T4/A10 | aggr A10 | aggr T4 | T4/A10 | TTFT p50 A10 | T4 | p95 A10 | T4 | cache A10 | T4 | fail A10/T4 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 69.6 | 36.4 | 0.52x | 68.9 | 36.0 | 0.52x | 88 | 140 | 94 | 144 | 100% | 99% | 0/0 |
| 5 | 26.5 | 14.4 | 0.54x | 132.8 | 71.7 | 0.54x | 182 | 381 | 214 | 449 | 100% | 99% | 0/0 |
| 8 | 20.0 | 9.9 | 0.49x | 159.1 | 78.6 | 0.49x | 225 | 576 | 294 | 728 | 100% | 99% | 0/0 |

_reps: A10 [2], T4 [1]; levels compared: [1, 5, 8]_

_not compared: A10-only ['F_cross', 'F_partial'], T4-only ['smoke']_

### qvac-q4km-cram0 (A10) vs qvac-q4km-cram0-c1k-np8 (T4)

Peak VRAM from gpu.csv: A10 14063 MiB, T4 5841 MiB

#### A_cold  (A10 file A_cold_r2.jsonl, T4 file A_cold_short.jsonl)

> SHAPE MISMATCH: prompt_tokens_target: A10 7000 vs T4 500

| conc | decode A10 | decode T4 | T4/A10 | aggr A10 | aggr T4 | T4/A10 | TTFT p50 A10 | T4 | p95 A10 | T4 | cache A10 | T4 | fail A10/T4 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 69.7 | 30.6 | 0.44x | 51.4 | 29.6 | 0.58x | 2619 | 554 | 2621 | 556 | 0% | 1% | 0/0 |
| 5 | 15.7 | 10.8 | 0.69x | 68.9 | 55.8 | 0.81x | 4506 | 2592 | 10756 | 2670 | 0% | 1% | 0/0 |
| 8 | 12.3 | 10.4 | 0.85x | 88.3 | 77.4 | 0.88x | 4836 | 4075 | 15341 | 4167 | 0% | 1% | 0/0 |

_reps: A10 [2], T4 [1]; levels compared: [1, 5, 8]_

#### A_warm  (A10 file A_warm_r2.jsonl, T4 file A_warm_short.jsonl)

> SHAPE MISMATCH: prompt_tokens_target: A10 7000 vs T4 500

| conc | decode A10 | decode T4 | T4/A10 | aggr A10 | aggr T4 | T4/A10 | TTFT p50 A10 | T4 | p95 A10 | T4 | cache A10 | T4 | fail A10/T4 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 69.6 | 30.7 | 0.44x | 68.9 | 30.5 | 0.44x | 88 | 129 | 94 | 131 | 100% | 97% | 0/0 |
| 5 | 26.5 | 14.6 | 0.55x | 132.8 | 72.2 | 0.54x | 182 | 359 | 214 | 396 | 100% | 97% | 0/0 |
| 8 | 20.0 | 10.4 | 0.52x | 159.1 | 82.7 | 0.52x | 225 | 582 | 294 | 654 | 100% | 97% | 0/0 |

_reps: A10 [2], T4 [1]; levels compared: [1, 5, 8]_

_not compared: A10-only ['F_cross', 'F_partial'], T4-only ['smoke']_

