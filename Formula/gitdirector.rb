class GitDirector < Formula
  include Language::Python::Virtualenv

  desc "Python CLI tool for managing and synchronizing multiple git repositories"
  homepage "https://github.com/anitoanto/gitdirector"
  url "https://github.com/anitoanto/gitdirector.git",
      tag:      "v0.1.5",
      revision: "PLACEHOLDER_COMMIT_SHA"
  license "MIT"

  depends_on "python@3.12"
  depends_on "git"

  def install
    venv = virtualenv_create(libexec, "python3.12")
    venv.pip_install_and_link buildpath
  end

  test do
    system bin/"gitdirector", "--help"
  end
end
