"""providers: one row per LLM provider (add yours here or in ~/.config/openapi/providers.toml) and the key file (environment first, then a private file)."""
from __future__ import annotations

import os
import stat
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

import logging

import catalog


# ------------------------------------------------------------------ providers: one row each, add yours here or in providers.toml
@dataclass(frozen=True)
class Provider:
    name: str
    base_url: str
    env: str                          # env var holding the key; "" = no key needed (a local server)
    prefer: tuple[str, ...] = ()      # substrings, best first: the first listed model matching one is the default
    free_only: bool = False           # list only models marked free (OpenRouter: ids ending in ":free")
    signup: str = ""
    models: tuple[str, ...] = ()      # for an API with no model list (Perplexity): these, without asking
    suspicious: bool = False          # operator unknown or no policy we could read: only used when asked for by name or with public=True, never with tools, and the prompt must pass privacy.leaks
    paid_only: bool = False           # no free tier at all: tried only after every free / free-tier provider failed
    api: str = "openai"               # "openai" = OpenAI chat protocol; others are adapters.py ADAPTERS (anthropic, gemini, cohere, cloudflare)

    def key(self) -> str | None:
        return (os.environ.get(self.env) or None) if self.env else "none"


logging.disable(logging.INFO)   # the http client logs every request at INFO; stdout belongs to the MCP protocol, stderr to errors

P = Provider
PROVIDERS: tuple[Provider, ...] = (
    # free tiers worth having first
    # no key, no login
    P("ollama", "http://localhost:11434/v1", "", (), signup="local: install ollama and pull a model, nothing to sign up for"),
    P("pollinations", "https://text.pollinations.ai/openai", "", ("openai",), signup="no login (anonymous tier, rate limited)"),
    P("groq", "https://api.groq.com/openai/v1", "GROQ_API_KEY", ("llama-3.3-70b", "gpt-oss-120b", "llama"), signup="https://console.groq.com/keys"),
    P("cerebras", "https://api.cerebras.ai/v1", "CEREBRAS_API_KEY", ("gpt-oss-120b", "llama", "qwen"), signup="https://cloud.cerebras.ai"),
    P("gemini", "https://generativelanguage.googleapis.com/v1beta/openai/", "GEMINI_API_KEY", ("gemini-2.5-flash", "flash"), signup="https://aistudio.google.com/apikey"),
    P("mistral", "https://api.mistral.ai/v1", "MISTRAL_API_KEY", ("mistral-small", "mistral-large", "mistral"), signup="https://console.mistral.ai/api-keys"),
    P("nvidia", "https://integrate.api.nvidia.com/v1", "NVIDIA_API_KEY", ("llama-3.3-70b", "llama", "deepseek"), signup="https://build.nvidia.com"),
    P("openrouter", "https://openrouter.ai/api/v1", "OPENROUTER_API_KEY", ("llama-3.3-70b", "gpt-oss", "deepseek", "qwen"), True, "https://openrouter.ai/keys"),
    P("sambanova", "https://api.sambanova.ai/v1", "SAMBANOVA_API_KEY", ("llama", "deepseek", "qwen"), signup="https://cloud.sambanova.ai/apis"),
    P("huggingface", "https://router.huggingface.co/v1", "HF_TOKEN", ("llama", "qwen", "deepseek"), signup="https://huggingface.co/settings/tokens"),
    P("github", "https://models.github.ai/inference", "GITHUB_TOKEN", ("gpt-4o", "llama"), signup="https://github.com/marketplace/models  (may be closed to new users)"),
    P("siliconflow", "https://api.siliconflow.com/v1", "SILICONFLOW_API_KEY", ("qwen", "deepseek", "glm"), signup="https://cloud.siliconflow.com"),
    P("zai", "https://open.bigmodel.cn/api/paas/v4", "ZAI_API_KEY", ("flash", "glm"), signup="https://z.ai/manage-apikey/apikey-list"),
    P("cohere", "https://api.cohere.ai/compatibility/v1", "COHERE_API_KEY", ("command-r", "command"), signup="https://dashboard.cohere.com/api-keys"),
    # free credits or a cheap paid tier
    P("together", "https://api.together.xyz/v1", "TOGETHER_API_KEY", ("llama-3.3-70b", "free", "llama"), signup="https://api.together.ai/settings/api-keys"),
    P("fireworks", "https://api.fireworks.ai/inference/v1", "FIREWORKS_API_KEY", ("llama", "qwen", "deepseek"), signup="https://fireworks.ai/account/api-keys"),
    P("hyperbolic", "https://api.hyperbolic.xyz/v1", "HYPERBOLIC_API_KEY", ("llama", "qwen", "deepseek"), signup="https://app.hyperbolic.xyz/settings"),
    P("novita", "https://api.novita.ai/v3/openai", "NOVITA_API_KEY", ("llama", "qwen", "deepseek"), signup="https://novita.ai/settings/key-management"),
    P("deepinfra", "https://api.deepinfra.com/v1/openai", "DEEPINFRA_API_KEY", ("llama-3.3-70b", "llama", "qwen"), signup="https://deepinfra.com/dash/api_keys"),
    P("nebius", "https://api.studio.nebius.com/v1", "NEBIUS_API_KEY", ("llama", "qwen", "deepseek"), signup="https://studio.nebius.com/settings/api-keys"),
    P("scaleway", "https://api.scaleway.ai/v1", "SCW_SECRET_KEY", ("llama", "qwen", "mistral"), signup="https://console.scaleway.com/iam/api-keys"),
    P("chutes", "https://llm.chutes.ai/v1", "CHUTES_API_KEY", ("deepseek", "qwen", "llama"), signup="https://chutes.ai"),
    P("featherless", "https://api.featherless.ai/v1", "FEATHERLESS_API_KEY", ("llama", "qwen"), signup="https://featherless.ai/account/api-keys"),
    P("venice", "https://api.venice.ai/api/v1", "VENICE_API_KEY", ("llama", "qwen", "deepseek"), signup="https://venice.ai/settings/api"),
    P("aimlapi", "https://api.aimlapi.com/v1", "AIMLAPI_KEY", ("llama", "gpt", "qwen"), signup="https://aimlapi.com/app/keys"),
    P("baseten", "https://inference.baseten.co/v1", "BASETEN_API_KEY", ("deepseek", "llama", "qwen"), signup="https://app.baseten.co/settings/api_keys"),
    P("dashscope", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "DASHSCOPE_API_KEY", ("qwen-plus", "qwen"), signup="https://bailian.console.alibabacloud.com"),
    P("moonshot", "https://api.moonshot.ai/v1", "MOONSHOT_API_KEY", ("kimi", "moonshot"), signup="https://platform.moonshot.ai/console/api-keys"),
    P("deepseek", "https://api.deepseek.com/v1", "DEEPSEEK_API_KEY", ("deepseek-chat", "deepseek"), signup="https://platform.deepseek.com/api_keys"),
    P("xai", "https://api.x.ai/v1", "XAI_API_KEY", ("grok",), signup="https://console.x.ai"),
    # more keyless: no login at all
    P("ovh", "https://oai.endpoints.kepler.ai.cloud.ovh.net/v1", "", ("gpt-oss", "llama", "qwen"), signup="no login: 2 req/min per IP per model; a key (ovhcloud.com/en/public-cloud/ai-endpoints) lifts it"),
    P("kilo", "https://api.kilo.ai/api/gateway", "", ("kilo-auto/free", "free"), signup="no login: ~200 req/hour per IP on free models; key at https://app.kilo.ai/profile"),
    P("blockrun", "https://blockrun.ai/api/v1", "", ("nemotron-3-super", "minimax", "nemotron"), free_only=True, signup="no login, no wallet: only the models it lists at price 0 are used (NVIDIA-hosted); paid ones on the same endpoint want a payment"),
    P("chat_at", "https://ch.at/v1", "", models=("gpt-4o-mini",), suspicious=True, signup="no login (ch.at, no model list: fixed gpt-4o-mini, small answers, tools ignored)"),
    P("apisfreellm", "https://api.apisfreellm.com/v1", "", models=("free-ai",), suspicious=True, signup="no login; unknown operator selling pooled keys: questions and research only, nothing private"),
    P("llm7", "https://api.llm7.io/v1", "", ("gpt-oss", "nemotron", "codestral"), signup="no login (turbo models); token at https://token.llm7.io lifts limits"),
    P("airforce", "https://api.airforce/v1", "", ("gpt", "llama"), signup="no login; free tier, often in outage"),
    # local servers: nothing to sign up for, each shows as down until you start it
    P("lmstudio", "http://localhost:1234/v1", "", (), signup="local: LM Studio -> Developer -> start server"),
    P("llamacpp", "http://localhost:8080/v1", "", (), signup="local: llama-server -m model.gguf  (also llamafile, LocalAI)"),
    P("jan", "http://localhost:1337/v1", "", (), signup="local: Jan -> Settings -> Local API Server"),
    P("vllm", "http://localhost:8000/v1", "", (), signup="local: vllm serve <model>"),
    # more free-key gateways
    P("requesty", "https://router.requesty.ai/v1", "REQUESTY_API_KEY", ("free",), signup="https://app.requesty.ai (200 req/day on zero-priced models, no card)"),
    P("kilo_key", "https://api.kilo.ai/api/gateway", "KILO_API_KEY", ("free",), signup="https://app.kilo.ai/profile"),
    P("llm7_key", "https://api.llm7.io/v1", "LLM7_API_KEY", ("gpt-oss", "nemotron", "codestral"), signup="https://token.llm7.io (higher limits than anonymous)"),
    P("aion", "https://api.aionlabs.ai/v1", "AION_API_KEY", (), signup="https://www.aionlabs.ai/app/api-keys/ (20K tokens/day, no card)"),
    P("ollama_cloud", "https://ollama.com/v1", "OLLAMA_API_KEY", ("gpt-oss", "qwen", "deepseek"), signup="https://ollama.com/settings/keys"),
    P("modelscope", "https://api-inference.modelscope.cn/v1", "MODELSCOPE_API_KEY", ("qwen", "deepseek"), signup="https://modelscope.cn/my/myaccesstoken (needs Alibaba Cloud binding + real-name check)"),
    P("gonka", "https://proxy.gonkabroker.com/v1", "GONKA_API_KEY", (), signup="https://app.gonkabroker.com/signup (SMS verification, ~1M tokens/month)"),
    P("inferencenet", "https://api.inference.net/v1", "INFERENCE_API_KEY", ("claude", "llama"), signup="https://inference.net"),
    P("vercel", "https://ai-gateway.vercel.sh/v1", "AI_GATEWAY_API_KEY", ("free",), signup="https://vercel.com/ai-gateway"),
    P("opencode", "https://opencode.ai/zen/v1", "OPENCODE_API_KEY", ("free",), signup="https://opencode.ai/zen (free models have a shared limit)"),
    # research / search-grounded
    P("perplexity", "https://api.perplexity.ai", "PERPLEXITY_API_KEY", ("sonar-deep-research", "sonar-reasoning-pro", "sonar-pro", "sonar"), signup="https://www.perplexity.ai/settings/api (paid credits)", models=("sonar", "sonar-pro", "sonar-reasoning-pro", "sonar-deep-research"), paid_only=True),
    # more gateways and hosts (URLs answered 200/401 on GET /models; signup pages from memory, not opened)
    P("parasail", "https://api.parasail.io/v1", "PARASAIL_API_KEY", ("deepseek", "qwen", "llama"), signup="https://www.saas.parasail.io/keys"),
    P("upstage", "https://api.upstage.ai/v1", "UPSTAGE_API_KEY", ("solar",), signup="https://console.upstage.ai/api-keys (free credits on signup)"),
    P("reka", "https://api.reka.ai/v1", "REKA_API_KEY", ("reka-flash", "reka"), signup="https://platform.reka.ai/apikeys"),
    P("sarvam", "https://api.sarvam.ai/v1", "SARVAM_API_KEY", ("sarvam",), signup="https://dashboard.sarvam.ai"),
    P("ai21", "https://api.ai21.com/studio/v1", "AI21_API_KEY", ("jamba",), signup="https://studio.ai21.com/account/api-key (trial credit)"),
    P("nscale", "https://inference.api.nscale.com/v1", "NSCALE_API_KEY", ("deepseek", "qwen", "llama"), signup="https://console.nscale.com"),
    P("friendli", "https://api.friendli.ai/serverless/v1", "FRIENDLI_TOKEN", ("llama", "qwen", "deepseek"), signup="https://friendli.ai/suite/~/personal-settings/tokens"),
    P("llmgateway", "https://api.llmgateway.io/v1", "LLMGATEWAY_API_KEY", ("free", "deepseek", "glm"), signup="https://llmgateway.io/dashboard"),
    P("minimax", "https://api.minimax.io/v1", "MINIMAX_API_KEY", ("minimax",), signup="https://platform.minimax.io"),
    P("stepfun", "https://api.stepfun.com/v1", "STEPFUN_API_KEY", ("step",), signup="https://platform.stepfun.com"),
    P("volcengine", "https://ark.cn-beijing.volces.com/api/v3", "ARK_API_KEY", ("doubao", "deepseek"), signup="https://console.volcengine.com/ark"),
    P("hunyuan", "https://api.hunyuan.cloud.tencent.com/v1", "HUNYUAN_API_KEY", ("hunyuan",), signup="https://console.cloud.tencent.com/hunyuan/api-key"),
    P("arliai", "https://api.arliai.com/v1", "ARLIAI_API_KEY", ("llama", "qwen"), signup="https://www.arliai.com/account"),
    P("nanogpt", "https://api.nano-gpt.com/v1", "NANOGPT_API_KEY", ("deepseek", "glm", "kimi"), signup="https://nano-gpt.com/api"),
    P("ppq", "https://api.ppq.ai/v1", "PPQ_API_KEY", ("deepseek", "glm", "kimi"), signup="https://ppq.ai/api-docs"),
    P("writer", "https://api.writer.com/v1", "WRITER_API_KEY", ("palmyra",), signup="https://app.writer.com/aistudio"),
    P("publicai", "https://api.publicai.co/v1", "PUBLICAI_API_KEY", ("apertus", "llama"), signup="https://platform.publicai.co"),
    P("neuralwatt", "https://api.neuralwatt.com/v1", "NEURALWATT_API_KEY", ("qwen", "llama"), signup="https://portal.neuralwatt.com"),
    P("gmi", "https://api.gmi-serving.com/v1", "GMI_API_KEY", ("deepseek", "glm", "kimi"), signup="https://console.gmicloud.ai"),
    P("cortecs", "https://api.cortecs.ai/v1", "CORTECS_API_KEY", ("llama", "mistral", "qwen"), signup="https://cortecs.ai"),
    P("inception", "https://api.inceptionlabs.ai/v1", "INCEPTION_API_KEY", ("mercury",), signup="https://platform.inceptionlabs.ai"),
    P("recursal", "https://api.recursal.com/v1", "RECURSAL_API_KEY", ("rwkv", "qwen"), signup="https://recursal.ai"),
    P("crofai", "https://ai.nahcrof.com/v2", "CROFAI_API_KEY", (), signup="https://ai.nahcrof.com"),
    P("lambda", "https://api.lambda.ai/v1", "LAMBDA_API_KEY", (), signup="https://cloud.lambda.ai"),
    P("kluster", "https://api.kluster.ai/v1", "KLUSTER_API_KEY", (), signup="https://platform.kluster.ai"),
    P("sensenova", "https://api.sensenova.cn/compatible-mode/v1", "SENSENOVA_API_KEY", (), signup="https://platform.sensenova.cn"),
    P("qianfan", "https://qianfan.baidubce.com/v2", "QIANFAN_API_KEY", (), signup="https://console.bce.baidu.com/qianfan"),
    P("yi", "https://api.lingyiwanwu.com/v1", "YI_API_KEY", (), signup="https://platform.lingyiwanwu.com"),
    P("modelhub", "https://api.modelhub.ai/v1", "MODELHUB_API_KEY", (), signup="https://modelhub.ai"),
    P("akash", "https://chatapi.akash.network/api/v1", "AKASH_API_KEY", (), signup="https://chat.akash.network"),
    P("openpipe", "https://app.openpipe.ai/api/v1", "OPENPIPE_API_KEY", (), signup="https://app.openpipe.ai"),
    P("predibase", "https://serving.app.predibase.com/v1", "PREDIBASE_API_KEY", (), signup="https://app.predibase.com"),
    P("anyscale", "https://api.endpoints.anyscale.com/v1", "ANYSCALE_API_KEY", (), signup="https://console.anyscale.com"),
    P("octoai", "https://text.octoai.run/v1", "OCTOAI_API_KEY", (), signup="https://octo.ai"),
    P("lepton", "https://api.lepton.ai/api/v1", "LEPTON_API_KEY", (), signup="https://dashboard.lepton.ai"),
    P("glhf", "https://glhf.chat/api/openai/v1", "GLHF_API_KEY", (), signup="https://glhf.chat"),
    P("shuttle", "https://api.shuttleai.com/v1", "SHUTTLE_API_KEY", (), signup="https://shuttleai.com"),
    P("pawan", "https://api.pawan.krd/v1", "PAWAN_API_KEY", (), signup="https://pawan.krd"),
    P("sreeshop", "https://api.sree.shop/v1", "SREE_API_KEY", (), signup="https://sree.shop"),
    P("unify", "https://api.unify.ai/v0", "UNIFY_API_KEY", (), signup="https://console.unify.ai"),
    P("martian", "https://withmartian.com/api/openai/v1", "MARTIAN_API_KEY", (), signup="https://withmartian.com"),
    P("litellm_proxy", "https://api.litellm.ai/v1", "LITELLM_API_KEY", (), signup="https://litellm.ai"),
    P("openai_hk", "https://api.openai-hk.com/v1", "OPENAIHK_API_KEY", (), signup="https://openai-hk.com"),
    P("gptapi_us", "https://api.gptapi.us/v1", "GPTAPIUS_API_KEY", (), signup="https://gptapi.us"),
    P("duckcoding", "https://jp.duckcoding.com/v1", "DUCKCODING_API_KEY", (), signup="https://duckcoding.com"),
    P("siliconcloud_gateway", "https://api.xiaoai.plus/v1", "XIAOAI_API_KEY", (), signup="https://xiaoai.plus"),
    P("moda", "https://api.mistral.ai/v1/fim", "MISTRAL_FIM_KEY", (), signup="https://console.mistral.ai"),
    P("codestral", "https://codestral.mistral.ai/v1", "CODESTRAL_API_KEY", (), signup="https://console.mistral.ai/codestral"),
    P("voyage", "https://api.voyageai.com/v1", "VOYAGE_API_KEY", (), signup="https://dash.voyageai.com"),
    P("nomic", "https://api-atlas.nomic.ai/v1", "NOMIC_API_KEY", (), signup="https://atlas.nomic.ai"),
    P("lm_proxy", "https://api.lm-proxy.com/v1", "LMPROXY_API_KEY", (), signup="https://lm-proxy.com"),
    P("tune", "https://proxy.tune.app/v1", "TUNE_API_KEY", (), signup="https://studio.tune.app"),
    P("monster", "https://llm.monsterapi.ai/v1", "MONSTER_API_KEY", (), signup="https://monsterapi.ai"),
    P("nlpcloud", "https://api.nlpcloud.io/v1", "NLPCLOUD_API_KEY", (), signup="https://nlpcloud.com"),
    P("gradient", "https://api.digitalocean.com/v1", "DO_API_KEY", (), signup="https://cloud.digitalocean.com"),
    P("crusoe", "https://api.crusoe.ai/v1", "CRUSOE_API_KEY", (), signup="https://crusoe.ai"),
    P("runpod", "https://api.runpod.ai/v2", "RUNPOD_API_KEY", (), signup="https://runpod.io"),
    P("neets", "https://api.neets.ai/v1", "NEETS_API_KEY", (), signup="https://neets.ai"),
    P("koboldcpp", "http://localhost:5001/v1", "", (), signup="https://github.com/LostRuins/koboldcpp"),
    P("tabbyapi", "http://localhost:5000/v1", "", (), signup="https://github.com/theroyallab/tabbyAPI"),
    P("sglang", "http://localhost:30000/v1", "", (), signup="https://github.com/sgl-project/sglang"),
    P("gpt4all", "http://localhost:4891/v1", "", (), signup="https://gpt4all.io"),
    P("msty", "http://localhost:10000/v1", "", (), signup="https://msty.app"),
    P("lemonade", "http://localhost:8000/api/v1", "", (), signup="https://lemonade-server.ai"),
    P("docker_mr", "http://localhost:12434/engines/v1", "", (), signup="https://docs.docker.com/ai/model-runner"),
    P("anthropic_vertex", "https://us-east5-aiplatform.googleapis.com/v1", "VERTEX_API_KEY", (), signup="https://console.cloud.google.com/vertex-ai"),
    P("azure_openai", "https://YOUR-RESOURCE.openai.azure.com/openai/v1", "AZURE_OPENAI_API_KEY", (), signup="https://portal.azure.com"),
    P("bedrock", "https://bedrock-runtime.us-east-1.amazonaws.com/openai/v1", "AWS_BEARER_TOKEN_BEDROCK", (), signup="https://console.aws.amazon.com/bedrock"),
    P("watsonx", "https://us-south.ml.cloud.ibm.com/ml/v1", "WATSONX_API_KEY", (), signup="https://cloud.ibm.com/watsonx"),
    P("oracle_genai", "https://inference.generativeai.us-chicago-1.oci.oraclecloud.com/v1", "OCI_GENAI_API_KEY", (), signup="https://cloud.oracle.com"),
    P("snowflake_cortex", "https://YOUR-ACCOUNT.snowflakecomputing.com/api/v2/cortex/v1", "SNOWFLAKE_PAT", (), signup="https://app.snowflake.com"),
    P("databricks", "https://YOUR-WORKSPACE.cloud.databricks.com/serving-endpoints", "DATABRICKS_TOKEN", (), signup="https://databricks.com"),
    P("sap_aicore", "https://api.ai.prod.eu-central-1.aws.ml.hana.ondemand.com/v2", "SAP_AICORE_KEY", (), signup="https://sap.com"),
    P("alephalpha", "https://api.aleph-alpha.com/v1", "ALEPH_ALPHA_API_KEY", (), signup="https://app.aleph-alpha.com"),
    P("jabir", "https://api.jabirproject.org/v1", "JABIR_API_KEY", (), signup="https://jabirproject.org"),
    P("exa", "https://api.exa.ai", "EXA_API_KEY", (), signup="https://dashboard.exa.ai"),
    P("tavily", "https://api.tavily.com", "TAVILY_API_KEY", (), signup="https://app.tavily.com"),
    P("brave_search", "https://api.search.brave.com/res/v1", "BRAVE_API_KEY", (), signup="https://brave.com/search/api"),
    P("serper", "https://google.serper.dev", "SERPER_API_KEY", (), signup="https://serper.dev"),
    P("firecrawl", "https://api.firecrawl.dev/v1", "FIRECRAWL_API_KEY", (), signup="https://firecrawl.dev"),
    P("jina_reader", "https://r.jina.ai", "JINA_READER_KEY", (), signup="https://jina.ai/reader"),
    P("you_com", "https://api.you.com/v1", "YDC_API_KEY", (), signup="https://you.com/platform"),
    P("elevenlabs", "https://api.elevenlabs.io/v1", "ELEVENLABS_API_KEY", (), signup="https://elevenlabs.io"),
    P("stability", "https://api.stability.ai/v1", "STABILITY_API_KEY", (), signup="https://platform.stability.ai"),
    P("fal", "https://fal.run", "FAL_KEY", (), signup="https://fal.ai/dashboard/keys"),
    P("assemblyai", "https://api.assemblyai.com/v2", "ASSEMBLYAI_API_KEY", (), signup="https://assemblyai.com"),
    P("deepgram", "https://api.deepgram.com/v1", "DEEPGRAM_API_KEY", (), signup="https://console.deepgram.com"),
    P("openweather", "https://api.openweathermap.org/data/2.5", "OPENWEATHER_API_KEY", (), signup="https://openweathermap.org/api"),
    P("deepbricks", "https://api.deepbricks.ai/v1", "DEEPBRICKS_API_KEY", (), signup="https://deepbricks.ai"),
    P("siray", "https://api.siray.ai/v1", "SIRAY_API_KEY", (), signup="https://siray.ai"),
    P("nebius_tf", "https://api.tokenfactory.nebius.com/v1", "NEBIUS_TF_API_KEY", (), signup="https://tokenfactory.nebius.com"),
    P("spark", "https://spark-api-open.xf-yun.com/v1", "SPARK_API_KEY", (), signup="https://console.xfyun.cn"),
    P("tencent_cloud", "https://api.lkeap.cloud.tencent.com/v1", "LKEAP_API_KEY", (), signup="https://cloud.tencent.com"),
    P("moonshot_cn", "https://api.moonshot.cn/v1", "MOONSHOT_CN_API_KEY", (), signup="https://platform.moonshot.cn"),
    P("siliconflow_cn", "https://api.siliconflow.cn/v1", "SILICONFLOW_CN_API_KEY", (), signup="https://cloud.siliconflow.cn"),
    P("dashscope_cn", "https://dashscope.aliyuncs.com/compatible-mode/v1", "DASHSCOPE_CN_API_KEY", (), signup="https://bailian.console.aliyun.com"),
    P("tokenflux", "https://tokenflux.ai/v1", "TOKENFLUX_API_KEY", (), signup="https://tokenflux.ai"),
    P("poe", "https://api.poe.com/v1", "POE_API_KEY", (), signup="https://poe.com/api_key"),
    P("zenmux", "https://zenmux.ai/api/v1", "ZENMUX_API_KEY", (), signup="https://zenmux.ai"),
    P("novita_ai", "https://api.novita.ai/openai/v1", "NOVITA_OAI_API_KEY", (), signup="https://novita.ai"),
    P("ppio", "https://api.ppinfra.com/v3/openai", "PPIO_API_KEY", (), signup="https://ppio.com"),
    P("infini", "https://cloud.infini-ai.com/maas/v1", "INFINI_API_KEY", (), signup="https://cloud.infini-ai.com"),
    P("mancer", "https://neuro.mancer.tech/oai/v1", "MANCER_API_KEY", (), signup="https://mancer.tech"),
    P("infermatic", "https://api.totalgpt.ai/v1", "INFERMATIC_API_KEY", (), signup="https://infermatic.ai"),
    P("zuki", "https://api.zukijourney.com/v1", "ZUKI_API_KEY", (), signup="https://zukijourney.com"),
    P("helixmind", "https://api.helixmind.online/v1", "HELIXMIND_API_KEY", (), signup="https://helixmind.online"),
    P("glama", "https://glama.ai/api/gateway/openai/v1", "GLAMA_API_KEY", (), signup="https://glama.ai"),
    P("helicone", "https://oai.helicone.ai/v1", "HELICONE_API_KEY", (), signup="https://helicone.ai"),
    P("edenai", "https://api.edenai.run/v2/llm", "EDENAI_API_KEY", (), signup="https://app.edenai.run"),
    P("cometapi", "https://api.cometapi.com/v1", "COMETAPI_API_KEY", (), signup="https://cometapi.com"),
    P("aihubmix", "https://aihubmix.com/v1", "AIHUBMIX_API_KEY", (), signup="https://aihubmix.com"),
    P("burncloud", "https://ai.burncloud.com/v1", "BURNCLOUD_API_KEY", (), signup="https://burncloud.com"),
    P("api2d", "https://openai.api2d.net/v1", "API2D_API_KEY", (), signup="https://api2d.com"),
    P("ohmygpt", "https://api.ohmygpt.com/v1", "OHMYGPT_API_KEY", (), signup="https://www.ohmygpt.com"),
    P("nextapi", "https://api.nextapi.fun/v1", "NEXTAPI_API_KEY", (), signup="https://nextapi.fun"),
    P("yunwu", "https://yunwu.ai/v1", "YUNWU_API_KEY", (), signup="https://yunwu.ai"),
    P("jina", "https://api.jina.ai/v1", "JINA_API_KEY", (), signup="https://jina.ai"),
    P("do_inference", "https://inference.do-ai.run/v1", "DO_INFERENCE_KEY", (), signup="https://cloud.digitalocean.com/gen-ai"),
    P("centml", "https://api.centml.com/openai/v1", "CENTML_API_KEY", (), signup="https://app.centml.com"),
    P("replicate", "https://api.replicate.com/v1", "REPLICATE_API_TOKEN", (), signup="https://replicate.com"),
    P("llama_api", "https://api.llama.com/compat/v1", "LLAMA_API_KEY", ("llama",), signup="https://llama.developer.meta.com"),
    P("baichuan", "https://api.baichuan-ai.com/v1", "BAICHUAN_API_KEY", ("baichuan",), signup="https://platform.baichuan-ai.com"),
    P("avian", "https://api.avian.io/v1", "AVIAN_API_KEY", ("deepseek", "llama"), signup="https://avian.io"),
    P("lemonfox", "https://api.lemonfox.ai/v1", "LEMONFOX_API_KEY", ("llama", "mixtral"), signup="https://www.lemonfox.ai"),
    P("electronhub", "https://api.electronhub.ai/v1", "ELECTRONHUB_API_KEY", ("gpt", "claude", "deepseek"), signup="https://electronhub.ai"),
    P("navy", "https://api.navy/v1", "NAVY_API_KEY", ("gpt", "claude"), signup="https://api.navy"),
    P("naga", "https://api.naga.ac/v1", "NAGA_API_KEY", ("gpt", "claude", "gemini"), signup="https://naga.ac"),
    P("mimo", "https://api.xiaomimimo.com/v1", "MIMO_API_KEY", ("mimo",), signup="https://platform.xiaomimimo.com"),
    P("berget", "https://api.berget.ai/v1", "BERGET_API_KEY", ("llama", "mistral"), signup="https://berget.ai"),
    P("fuelix", "https://api.fuelix.ai/v1", "FUELIX_API_KEY", ("gpt", "claude"), signup="https://fuelix.ai"),
    P("ai302", "https://api.302.ai/v1", "AI302_API_KEY", ("gpt", "claude", "deepseek"), signup="https://302.ai"),
    P("chatanywhere", "https://api.chatanywhere.tech/v1", "CHATANYWHERE_API_KEY", ("gpt", "deepseek"), signup="https://github.com/chatanywhere/GPT_API_free"),
    P("zhizengzeng", "https://api.zhizengzeng.com/v1", "ZHIZENGZENG_API_KEY", ("gpt", "claude"), signup="https://zhizengzeng.com"),
    P("atlascloud", "https://api.atlascloud.ai/v1", "ATLASCLOUD_API_KEY", ("deepseek", "qwen", "glm"), signup="https://www.atlascloud.ai"),
    P("ionet", "https://api.intelligence.io.solutions/api/v1", "IOINTELLIGENCE_API_KEY", ("deepseek", "llama", "qwen"), signup="https://ai.io.net"),
    P("vsegpt", "https://api.vsegpt.ru/v1", "VSEGPT_API_KEY", ("gpt", "claude"), signup="https://vsegpt.ru"),
    P("portkey", "https://api.portkey.ai/v1", "PORTKEY_API_KEY", ("gpt",), signup="https://app.portkey.ai"),
    # native APIs through adapters.py
    P("anthropic", "https://api.anthropic.com/v1", "ANTHROPIC_API_KEY", ("haiku", "sonnet"), signup="https://console.anthropic.com/settings/keys (paid; a Claude subscription is not an API key)", api="anthropic", paid_only=True),
    P("cloudflare", "https://api.cloudflare.com/client/v4/accounts/{CLOUDFLARE_ACCOUNT_ID}/ai", "CLOUDFLARE_API_TOKEN", ("llama", "qwen", "gpt-oss"), signup="https://dash.cloudflare.com/profile/api-tokens (also set CLOUDFLARE_ACCOUNT_ID; 10k neurons/day free)", api="cloudflare"),
    P("openai", "https://api.openai.com/v1", "OPENAI_API_KEY", ("gpt-4o-mini", "gpt"), signup="https://platform.openai.com/api-keys (paid)", paid_only=True),
)


def catalog_providers(rows: list[dict] | None = None) -> list[Provider]:
    """The providers that providers.json adds to the built-in tuple (a name the code already defines is ignored here). A broken file is reported on stderr and skipped: it must never take the server down."""
    try:
        rows = catalog.load() if rows is None else rows
    except ValueError as e:
        print(f"openapi: ignoring catalog: {e}", file=sys.stderr)
        return []
    return [Provider(r["name"], r["base_url"], r["env"], tuple(r.get("prefer", ())), bool(r.get("free_only", False)), r.get("signup", ""), tuple(r.get("models", ())), r.get("api", "openai"))
            for r in catalog.extra_rows(rows, {p.name for p in PROVIDERS})]


def user_providers(path: Path | None = None) -> tuple[Provider, ...]:
    """Built-in providers, then the extras in providers.json, then ~/.config/openapi/providers.toml (OPENAPI_PROVIDERS = another path); a row with an earlier name replaces it.
    Any OpenAI-compatible endpoint, a local Ollama too (env = "" means no key):
        [[provider]]
        name = "ollama"; base_url = "http://localhost:11434/v1"; env = ""
    """
    path = path or Path(os.environ.get("OPENAPI_PROVIDERS", Path.home() / ".config/openapi/providers.toml"))
    out = {p.name: p for p in PROVIDERS}
    out.update({p.name: p for p in catalog_providers()})
    try:
        data = tomllib.loads(path.read_text())
    except FileNotFoundError:
        return tuple(out.values())
    for row in data.get("provider", []):
        try:
            out[row["name"]] = Provider(row["name"], row["base_url"], row["env"], tuple(row.get("prefer", ())), bool(row.get("free_only", False)), row.get("signup", ""), tuple(row.get("models", ())), row.get("api", "openai"))
        except KeyError as e:
            raise ValueError(f"{path}: a [[provider]] needs name, base_url and env (missing {e})") from None
    return tuple(out.values())


# ------------------------------------------------------------------ keys: environment first, then a private file
def keys_path() -> Path:
    return Path(os.environ.get("OPENAPI_KEYS", Path.home() / ".config/openapi/keys.env"))


def load_key_file(path: Path | None = None) -> int:
    """Put KEY=VALUE lines of the key file into os.environ (the environment wins). Refuses a file others can read. Returns how many were set."""
    path = path or keys_path()
    try:
        st = path.stat()
    except OSError:
        return 0
    if st.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise PermissionError(f"{path}: readable by others, chmod 600 it")
    n = 0
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip().removeprefix("export "), v.strip().strip("'\"")
        if k and v and k not in os.environ:
            os.environ[k] = v
            n += 1
    return n


def set_key(env: str, value: str, path: Path | None = None) -> Path:
    """Write ENV=value into the key file (created mode 600, other lines kept, an old line for the same variable replaced)."""
    path = path or keys_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [l for l in (path.read_text().splitlines() if path.exists() else []) if l.split("=", 1)[0].strip().removeprefix("export ") != env]
    lines.append(f"{env}={value}")
    tmp = path.with_name(path.name + ".tmp")
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "w") as f:
        f.write("\n".join(lines) + "\n")
    os.replace(tmp, path)
    return path


