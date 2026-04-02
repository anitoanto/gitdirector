class Gitdirector < Formula
  desc "A Python CLI tool for managing and synchronizing multiple git repositories"
  homepage "https://github.com/anitoanto/gitdirector"
  url "https://github.com/anitoanto/gitdirector.git", branch: "main"
  version "0.1.4"
  license "MIT"

  depends_on "python@3.12"
  depends_on "git"

  def install
    venv = virtualenv_create(libexec, "python3.12")
    venv.pip_install_and_link buildpath
  end

  def post_install
    bin.env_script_all_files(libexec/"bin", PATH: "#{libexec}/bin:$PATH")
  end

  test do
    system bin/"gitdirector", "--help"
  end
end
