# RELAY400 — fix HTTP 400 from reasoning_content relay on opencode gateway
- [x] Root cause: opencode.ai/zen/v1 rejects `reasoning_content` in assistant msgs (relay_reasoning=True) -> 400 -> agent interrupted
- [x] Applied: relay_reasoning=False for opencode/deepseek-v4-flash-free (persisted via ProviderManager)
- [ ] USER ACTION: restart QwenPaw (qwenpaw shutdown + relaunch TUI with --resume) to load config
- [ ] OSRM deploy: NOT done — frontend only draws road via public OSRM; backend routing still Haversine; no local OSRM server
