# Context profiles

`docker-compose.context-exp.yml` is an isolated backend for context experiments. It does not
change the default Chumba stack and has no egress network.

## 64K profile

```powershell
docker compose stop llama-server llama-port
docker compose -f experiments/context-profiles/docker-compose.context-exp.yml `
  --env-file experiments/context-profiles/context-exp.env.example up -d
python scripts/bench_context.py --base-url http://127.0.0.1:8090/v1 `
  --tests perf,retrieval,multi_turn,tools --fills 0.25,0.5,0.85 --repeats 1 `
  --stream --ctx-size 65536 --container chumba-llama-exp
```

Stop the experiment and restore the default profile:

```powershell
docker compose -f experiments/context-profiles/docker-compose.context-exp.yml `
  --env-file experiments/context-profiles/context-exp.env.example down
docker compose start llama-server llama-port
```

The current `.env` is not used by this profile. Override `EXP_CTX`, `EXP_NGL`, `EXP_KV_*`,
`EXP_KV_OFFLOAD`, `EXP_PARALLEL`, `EXP_FA`, `EXP_SLEEP_IDLE_SECONDS` and `EXP_PORT` in a local
`context-exp.env` if needed. Do not commit that file; it is ignored.

## Safety

- Run only one GPU backend at a time.
- 64K leaves roughly 650 MiB VRAM free on a 16 GB card; close other GPU-heavy programs.
- The profile publishes only `127.0.0.1:8090` and uses an internal Docker network plus a socat
  forwarder, matching the default stack's isolation pattern.
- Full results and methodology: `docs/experiments/context-expansion.md`.
