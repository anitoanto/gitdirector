class Gitdirector < Formula
  include Language::Python::Virtualenv
  desc "A terminal based control plane for developers working across multiple repositories. Launch multiple AI coding agents, multiple tmux sessions and track changes across all your repos in one place."
  homepage "https://github.com/anitoanto/gitdirector"
  url "https://files.pythonhosted.org/packages/61/5f/1a81b5c03b3da66d3d9ce96d6ebcd1b7dd452a2ee944713f98d3ba43b4cd/gitdirector-1.5.2.tar.gz"
  sha256 "fd1a06451ddc977cbe958291ef5447f9703fcbd561d10a93ba6dd743cbe6bbe4"
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
