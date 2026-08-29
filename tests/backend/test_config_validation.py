"""
tests/backend/test_config_validation.py
---------------------------------------
Phase 7 tests for ConfigValidator and fail-fast startup rules.
"""

import pytest
from pathlib import Path

from backend.config import Settings
from backend.utils.config_validation import ConfigValidator, ConfigValidationError


class TestConfigValidation:
    """Test configuration validation rules."""

    def test_production_fails_when_auth_disabled(self):
        cfg = Settings(app_env="production", auth_enabled=False)
        validator = ConfigValidator(cfg=cfg)
        valid, results = validator.validate()
        assert valid is False
        fail_rule = next(r for r in results if r["rule"] == "prod_auth_enabled")
        assert fail_rule["status"] == "FAIL"

        with pytest.raises(ConfigValidationError):
            validator.enforce_or_exit()

    def test_production_fails_on_cors_wildcard(self):
        cfg = Settings(app_env="production", auth_enabled=True, cors_origins="*")
        validator = ConfigValidator(cfg=cfg)
        valid, results = validator.validate()
        assert valid is False
        fail_rule = next(r for r in results if r["rule"] == "cors_wildcard_in_prod")
        assert fail_rule["status"] == "FAIL"

    def test_dev_mode_warns_on_cors_wildcard_but_passes(self):
        cfg = Settings(app_env="development", auth_enabled=True, cors_origins="*")
        validator = ConfigValidator(cfg=cfg)
        valid, results = validator.validate()
        assert valid is True
        warn_rule = next(r for r in results if r["rule"] == "cors_wildcard_in_dev")
        assert warn_rule["status"] == "WARN"

    def test_invalid_port_fails(self):
        cfg = Settings(backend_port=70000)
        validator = ConfigValidator(cfg=cfg)
        valid, results = validator.validate()
        assert valid is False
        fail_rule = next(r for r in results if r["rule"] == "backend_port_range")
        assert fail_rule["status"] == "FAIL"

    def test_valid_development_config_passes(self):
        cfg = Settings(app_env="development", auth_enabled=True, cors_origins="http://localhost:5173", backend_port=8000)
        validator = ConfigValidator(cfg=cfg)
        valid, results = validator.validate()
        assert valid is True
        assert all(r["status"] in ("PASS", "WARN") for r in results)
