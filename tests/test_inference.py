"""vLLM adapter: identity from /v1/models + /version + the HF cache, serving state from /metrics."""
import asyncio
import json

import httpx

from nanogate.inference import LocalModelAdapter, hf_snapshot_info, parse_prometheus

MODEL = "Qwen/Qwen2.5-3B-Instruct"
SHA = "aa8e72537993ba99e69dfaafa59ed015b17504d1"

METRICS = f"""# HELP vllm:num_requests_running Number of requests in model execution batches.
# TYPE vllm:num_requests_running gauge
vllm:num_requests_running{{engine="0",model_name="{MODEL}"}} 2.0
vllm:num_requests_waiting{{engine="0",model_name="{MODEL}"}} 1.0
vllm:kv_cache_usage_perc{{engine="0",model_name="{MODEL}"}} 0.125
vllm:kv_cache_usage_perc{{engine="0",model_name="other/model"}} 0.9
process_resident_memory_bytes 1.5e+09
"""


def fake_hf_cache(root, repo=MODEL, sha=SHA, quantized=False):
    d = root / "hub" / ("models--" + repo.replace("/", "--"))
    (d / "refs").mkdir(parents=True)
    (d / "refs" / "main").write_text(sha)
    snap = d / "snapshots" / sha
    blobs = d / "blobs"
    snap.mkdir(parents=True)
    blobs.mkdir()
    cfg = {"torch_dtype": "bfloat16", "model_type": "qwen2"}
    if quantized:
        cfg["quantization_config"] = {"quant_method": "awq"}
    for name, content in {"config.json": json.dumps(cfg),
                          "model.safetensors.index.json": json.dumps({"metadata": {"total_size": 6_171_877_376}}),
                          "model-00001-of-00001.safetensors": "x" * 100}.items():
        (blobs / name).write_text(content)
        (snap / name).symlink_to(blobs / name)   # HF cache layout: snapshot files link to blobs


def test_parse_prometheus_filters_other_models():
    m = parse_prometheus(METRICS, MODEL)
    assert m["vllm:kv_cache_usage_perc"] == 0.125
    assert m["vllm:num_requests_running"] == 2.0
    assert m["process_resident_memory_bytes"] == 1.5e9   # unlabelled samples are kept


def test_hf_snapshot_info(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    fake_hf_cache(tmp_path)
    info = hf_snapshot_info(MODEL)
    assert info["digest"] == SHA
    assert info["quantization"] == "bfloat16"
    assert info["parameter_size"] == "3.1B"
    assert info["size_bytes"] > 0
    assert hf_snapshot_info("nobody/missing") == {}


def test_hf_snapshot_info_quantized_has_no_parameter_guess(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    fake_hf_cache(tmp_path, quantized=True)
    info = hf_snapshot_info(MODEL)
    assert info["quantization"] == "awq" and "parameter_size" not in info


def mock_vllm(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/v1/models":
        return httpx.Response(200, json={"object": "list", "data": [
            {"id": MODEL, "object": "model", "owned_by": "vllm", "root": MODEL, "max_model_len": 8192}]})
    if request.url.path == "/version":
        return httpx.Response(200, json={"version": "0.0.test"})
    if request.url.path == "/metrics":
        return httpx.Response(200, text=METRICS)
    return httpx.Response(404)


def test_identify_and_placement(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HOME", str(tmp_path))
    fake_hf_cache(tmp_path)

    async def go():
        a = LocalModelAdapter("http://vllm.test/v1", MODEL, family="qwen2.5")
        a._client = httpx.AsyncClient(transport=httpx.MockTransport(mock_vllm))
        info = await a.identify()
        placement = await a.placement()
        health = await a.health()
        return a, info, placement, health

    a, info, placement, health = asyncio.run(go())
    assert info["runtime"] == "vllm" and info["runtime_version"] == "0.0.test"
    assert info["context_length"] == 8192 and info["digest"] == SHA
    assert a.revision == SHA[:12]
    assert placement == {"loaded": True, "kv_cache_usage": 0.125, "requests_running": 2.0, "requests_waiting": 1.0}
    assert health["state"] == "ready"


def test_placement_unreachable_is_unknown():
    async def go():
        a = LocalModelAdapter("http://vllm.test/v1", MODEL)
        a._client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
        return await a.placement()
    assert asyncio.run(go())["loaded"] is None

