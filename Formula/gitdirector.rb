class Gitdirector < Formula
  include Language::Python::Virtualenv
  desc "A terminal based control plane for developers working across multiple repositories. Launch multiple AI coding agents, multiple tmux sessions and track changes across all your repos in one place."
  homepage "https://github.com/anitoanto/gitdirector"
  url "https://github.com/anitoanto/gitdirector.git",
      origin: "https://github.com/anitoanto/gitdirector.git",
      tag:      "v1.1.2",
      revision: "40fa165ef75b786580e15a1cf694933411780df5"
  license "MIT"

  depends_on "python@3.12"
  depends_on "uv"

  def install
    # Create a proper virtualenv so the script shebang points to it
    venv = virtualenv_create(libexec, "python3.12")

    # Use uv to install — it knows how to handle uv_build natively
    system Formula["uv"].opt_bin/"uv", "pip", "install",
           "--python", "#{libexec}/bin/python3.12",
           "--no-cache",
           buildpath

    # Symlink the entry point script
    bin.install_symlink libexec/"bin/gitdirector"
  end

  test do
    system bin/"gitdirector", "--help"
  end
end