from handlers.models import POPULAR_MODELS


def test_qwen_token_plan_models_are_available_in_picker() -> None:
    models = POPULAR_MODELS["qwen_code"]

    assert models[0] == "qwen3.8-max-preview"
    assert {"qwen3.7-max", "qwen3.7-plus", "qwen3.6-flash", "deepseek-v4-pro", "glm-5.2"} <= set(
        models
    )
