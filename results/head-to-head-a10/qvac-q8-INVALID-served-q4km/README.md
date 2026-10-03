# INVALID — these runs served Q4_K_M, not Q8_0

`scripts/h2h_serve.sh` sourced `/root/h2h/env.sh` after the caller set
`GGUF=/root/models/Qwen3-8B-Q8_0.gguf`, and env.sh reset it to the Q4_K_M path.
Every launch command in `logs/cmds/q8-*.cmd` shows `-m .../Qwen3-8B-Q4_K_M.gguf`;
VRAM (14,051 MiB) and quality output (40/40 identical to Q4_K_M) agree.

The data here are additional Q4_K_M runs (valid as such, labelled wrong) and
must NOT be quoted as Q8_0. Fixed in h2h_serve.sh (caller overrides now win).
