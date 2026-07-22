from pathlib import Path

import pytest
import yaml

from gitdirector.config import Config


class TestConfigInit:
    def test_creates_config_dir(self, config_dir, config):
        assert config_dir.is_dir()

    def test_creates_config_file_on_save(self, config):
        config.save()
        assert config.config_file.exists()

    def test_empty_config_defaults(self, config):
        assert config.repositories == []
        assert config.max_workers == Config.DEFAULT_MAX_WORKERS
        assert config.theme == Config.DEFAULT_THEME
        assert config.github_username is None
        assert config.github_PAT is None

    def test_loads_existing_config(self, config_dir, monkeypatch):
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "config.yaml"
        secrets_file = config_dir / "secrets.yaml"
        config_file.write_text(
            yaml.dump(
                {
                    "repositories": ["/tmp/repo-a", "/tmp/repo-b"],
                    "max_workers": 4,
                }
            )
        )
        secrets_file.write_text(
            yaml.dump(
                {
                    "github_username": "octocat",
                    "github_PAT": "ghp_secret",
                }
            )
        )

        monkeypatch.setattr(Path, "home", lambda: config_dir.parent)
        cfg = Config()

        assert len(cfg.repositories) == 2
        assert cfg.repositories[0] == Path("/tmp/repo-a")
        assert cfg.repositories[1] == Path("/tmp/repo-b")
        assert cfg.max_workers == 4
        assert cfg.github_username == "octocat"
        assert cfg.github_PAT == "ghp_secret"

    def test_loads_empty_yaml_file(self, config_dir, monkeypatch):
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "config.yaml").write_text("")
        monkeypatch.setattr(Path, "home", lambda: config_dir.parent)
        cfg = Config()
        assert cfg.repositories == []
        assert cfg.max_workers == Config.DEFAULT_MAX_WORKERS

    @pytest.mark.parametrize(
        ("main_data", "secrets_data", "message"),
        [
            ({"repositories": "/tmp/repo"}, None, "Invalid repositories"),
            ({"repositories": ["", "/tmp/repo"]}, None, "Invalid repositories"),
            ({"repositories": [1]}, None, "Invalid repositories"),
            ({"max_workers": True}, None, "Invalid max_workers"),
            ({"max_workers": "4"}, None, "Invalid max_workers"),
            ({"max_workers": 33}, None, "Invalid max_workers"),
            ({"theme": None}, None, "Invalid theme"),
            ({"theme": "   "}, None, "Invalid theme"),
            (None, {"github_username": 1}, "Invalid github_username"),
            (None, {"github_PAT": []}, "Invalid github_PAT"),
        ],
    )
    def test_rejects_malformed_loaded_fields(
        self, config_dir, monkeypatch, main_data, secrets_data, message
    ):
        config_dir.mkdir(parents=True, exist_ok=True)
        if main_data is not None:
            (config_dir / "config.yaml").write_text(yaml.dump(main_data))
        if secrets_data is not None:
            (config_dir / "secrets.yaml").write_text(yaml.dump(secrets_data))
        monkeypatch.setattr(Path, "home", lambda: config_dir.parent)

        with pytest.raises(ValueError, match=message):
            Config()

    def test_blank_github_credentials_load_as_none(self, config_dir, monkeypatch):
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "secrets.yaml").write_text(
            yaml.dump({"github_username": "  ", "github_PAT": ""})
        )
        monkeypatch.setattr(Path, "home", lambda: config_dir.parent)

        cfg = Config()

        assert cfg.github_username is None
        assert cfg.github_PAT is None


class TestConfigReloadValidation:
    @pytest.mark.parametrize(
        ("filename", "data", "message"),
        [
            ("config.yaml", {"repositories": "not-a-list"}, "Invalid repositories"),
            ("config.yaml", {"max_workers": False}, "Invalid max_workers"),
            ("config.yaml", {"theme": ""}, "Invalid theme"),
            ("secrets.yaml", {"github_username": 1}, "Invalid github_username"),
            ("secrets.yaml", {"github_PAT": {}}, "Invalid github_PAT"),
        ],
    )
    def test_rejects_malformed_fields_reloaded_under_lock(self, config, filename, data, message):
        (config.config_dir / filename).write_text(yaml.dump(data))

        with pytest.raises(ValueError, match=message):
            config.add_repository(Path("/tmp/repo"))


class TestConfigAddRepository:
    def test_add_new_repository(self, config):
        result = config.add_repository(Path("/tmp/repo"))
        assert result is True
        assert Path("/tmp/repo") in config.repositories

    def test_add_duplicate_returns_false(self, config):
        config.add_repository(Path("/tmp/repo"))
        result = config.add_repository(Path("/tmp/repo"))
        assert result is False
        assert config.repositories.count(Path("/tmp/repo")) == 1

    def test_add_persists_to_disk(self, config):
        config.add_repository(Path("/tmp/repo"))
        data = yaml.safe_load(config.config_file.read_text())
        assert "/tmp/repo" in data["repositories"]


class TestConfigRemoveRepository:
    def test_remove_existing(self, config):
        config.add_repository(Path("/tmp/repo"))
        result = config.remove_repository(Path("/tmp/repo"))
        assert result is True
        assert Path("/tmp/repo") not in config.repositories

    def test_remove_nonexistent_returns_false(self, config):
        result = config.remove_repository(Path("/tmp/missing"))
        assert result is False

    def test_remove_persists_to_disk(self, config):
        config.add_repository(Path("/tmp/repo"))
        config.remove_repository(Path("/tmp/repo"))
        data = yaml.safe_load(config.config_file.read_text())
        assert data["repositories"] == []


class TestConfigHasRepository:
    def test_has_returns_true(self, config):
        config.add_repository(Path("/tmp/repo"))
        assert config.has_repository(Path("/tmp/repo")) is True

    def test_has_returns_false(self, config):
        assert config.has_repository(Path("/tmp/repo")) is False


class TestConfigClear:
    def test_clear_removes_all(self, config):
        config.add_repository(Path("/tmp/a"))
        config.add_repository(Path("/tmp/b"))
        config.clear()
        assert config.repositories == []

    def test_clear_persists_to_disk(self, config):
        config.add_repository(Path("/tmp/a"))
        config.clear()
        data = yaml.safe_load(config.config_file.read_text())
        assert data["repositories"] == []


class TestConfigTheme:
    def test_default_theme(self, config):
        assert config.theme == "rose-pine"

    def test_loads_custom_theme(self, config_dir, monkeypatch):
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "config.yaml"
        data = {"repositories": [], "theme": "dracula"}
        config_file.write_text(yaml.dump(data))

        monkeypatch.setattr(Path, "home", lambda: config_dir.parent)
        cfg = Config()
        assert cfg.theme == "dracula"

    def test_saves_custom_theme(self, config):
        config.theme = "nord"
        config.save()
        data = yaml.safe_load(config.config_file.read_text())
        assert data["theme"] == "nord"

    def test_default_theme_not_saved(self, config):
        config.save()
        data = yaml.safe_load(config.config_file.read_text())
        assert "theme" not in data

    def test_missing_theme_uses_default(self, config_dir, monkeypatch):
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "config.yaml"
        data = {"repositories": []}
        config_file.write_text(yaml.dump(data))

        monkeypatch.setattr(Path, "home", lambda: config_dir.parent)
        cfg = Config()
        assert cfg.theme == Config.DEFAULT_THEME


class TestConfigGitHubAuthCleanup:
    def test_saves_github_auth(self, config):
        config.github_username = "octocat"
        config.github_PAT = "ghp_secret"
        config.save()

        assert "github_username" not in yaml.safe_load(config.config_file.read_text())
        secrets_data = yaml.safe_load(config.secrets_file.read_text())
        assert secrets_data["github_username"] == "octocat"
        assert secrets_data["github_PAT"] == "ghp_secret"

    def test_preserves_github_auth_when_repositories_change(self, config_dir, monkeypatch):
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "config.yaml").write_text(yaml.dump({"repositories": ["/tmp/repo-a"]}))
        (config_dir / "secrets.yaml").write_text(
            yaml.dump(
                {
                    "github_username": "octocat",
                    "github_PAT": "ghp_secret",
                }
            )
        )
        monkeypatch.setattr(Path, "home", lambda: config_dir.parent)
        cfg = Config()

        cfg.add_repository(Path("/tmp/repo-b"))

        main_data = yaml.safe_load(cfg.config_file.read_text())
        secrets_data = yaml.safe_load(cfg.secrets_file.read_text())
        assert main_data["repositories"] == ["/tmp/repo-a", "/tmp/repo-b"]
        assert secrets_data["github_username"] == "octocat"
        assert secrets_data["github_PAT"] == "ghp_secret"

    def test_secrets_file_not_created_when_no_github_auth(self, config):
        config.save()

        assert config.config_file.exists()
        assert not config.secrets_file.exists()


class TestConfigSaveRoundtrip:
    def test_roundtrip(self, config_dir, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: config_dir.parent)
        cfg1 = Config()
        cfg1.add_repository(Path("/tmp/repo-x"))
        cfg1.add_repository(Path("/tmp/repo-y"))

        cfg2 = Config()
        assert cfg2.repositories == [Path("/tmp/repo-x"), Path("/tmp/repo-y")]

    def test_max_workers_roundtrip(self, config_dir, monkeypatch):
        monkeypatch.setattr(Path, "home", lambda: config_dir.parent)
        cfg1 = Config()
        cfg1.max_workers = 5
        cfg1.save()

        cfg2 = Config()
        assert cfg2.max_workers == 5

    def test_default_max_workers_not_written(self, config):
        config.save()
        data = yaml.safe_load(config.config_file.read_text())
        assert "max_workers" not in data


class TestConfigGitHubAuth:
    def test_clearing_auth_deletes_secrets_file(self, config_dir, monkeypatch):
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "config.yaml").write_text(yaml.dump({"repositories": []}))
        (config_dir / "secrets.yaml").write_text(
            yaml.dump({"github_username": "octocat", "github_PAT": "ghp_secret"})
        )
        monkeypatch.setattr(Path, "home", lambda: config_dir.parent)

        config = Config()
        config.github_username = None
        config.github_PAT = None
        config.save()

        assert not config.secrets_file.exists()

    def test_clearing_pat_preserves_username(self, config_dir, monkeypatch):
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "config.yaml").write_text(yaml.dump({"repositories": []}))
        (config_dir / "secrets.yaml").write_text(
            yaml.dump({"github_username": "octocat", "github_PAT": "ghp_secret"})
        )
        monkeypatch.setattr(Path, "home", lambda: config_dir.parent)

        config = Config()
        config.github_PAT = None
        config.save()

        secrets = yaml.safe_load(config.secrets_file.read_text())
        assert "github_PAT" not in secrets
        assert secrets["github_username"] == "octocat"
