"""
tests/backend/test_config.py
----------------------------
Tests for configuration defaults, CORS parsing, and relative paths.
"""

def test_settings_defaults():
    from backend.config import Settings
    s = Settings()
    assert s.app_name == "SovereignAIWorkbench"
    assert s.backend_port == 8000
    assert "localhost:5173" in s.cors_origins


def test_cors_origins_list():
    from backend.config import Settings
    s = Settings(cors_origins="http://localhost:5173,http://localhost:3000")
    origins = s.cors_origins_list
    assert "http://localhost:5173" in origins
    assert "http://localhost:3000" in origins
    assert len(origins) == 2


def test_project_root_paths_are_relative():
    from backend.config import Settings, PROJECT_ROOT
    s = Settings()
    assert str(s.upload_dir).startswith(str(PROJECT_ROOT))
    assert str(s.log_dir).startswith(str(PROJECT_ROOT))
    assert str(s.sandbox_dir).startswith(str(PROJECT_ROOT))
