import inspect

from semantic_prompt_transfer.poc_bootstrap import build_colab_poc


def test_review_bootstrap_does_not_use_fallback_generator():
    source = inspect.getsource(build_colab_poc)
    assert "FallbackGenerator(" not in source
    assert "text_generator: TextGenerator = generator" in source
    assert "text_generator = primary_generator" in source


def test_generation_and_verification_roles_are_separate():
    source = inspect.getsource(build_colab_poc)
    assert "verification_generator=verification_generator or primary_generator" in source
