from pathlib import Path

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

    def test_loads_existing_config(self, config_dir, monkeypatch):
        config_dir.mkdir(parents=True, exist_ok=True)
        config_file = config_dir / "config.yaml"
        data = {"repositories": ["/tmp/repo-a", "/tmp/repo-b"], "max_workers": 4}
        config_file.write_text(yaml.dump(data))

        monkeypatch.setattr(Path, "home", lambda: config_dir.parent)
        cfg = Config()

        assert len(cfg.repositories) == 2
        assert cfg.repositories[0] == Path("/tmp/repo-a")
        assert cfg.repositories[1] == Path("/tmp/repo-b")
        assert cfg.max_workers == 4

    def test_loads_empty_yaml_file(self, config_dir, monkeypatch):
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / "config.yaml").write_text("")
        monkeypatch.setattr(Path, "home", lambda: config_dir.parent)
        cfg = Config()
        assert cfg.repositories == []
        assert cfg.max_workers == Config.DEFAULT_MAX_WORKERS


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
