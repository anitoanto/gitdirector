FROM python:3.12-slim-bookworm

ARG TMUX_VERSION=3.7c

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      build-essential pkg-config libevent-dev libncurses-dev bison curl ca-certificates \
      zsh bash procps ncurses-term ncurses-bin git sudo util-linux psmisc lsof rsync gdb \
 && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL "https://github.com/tmux/tmux/releases/download/${TMUX_VERSION}/tmux-${TMUX_VERSION}.tar.gz" \
      | tar xz -C /tmp \
 && cd "/tmp/tmux-${TMUX_VERSION}" \
 && ./configure --prefix=/usr/local \
 && make -j"$(nproc)" \
 && make install \
 && rm -rf /tmp/tmux-*

RUN pip install --no-cache-dir uv

# Non-root, so RLIMIT_NPROC applies; zsh login shells like the user's Mac.
RUN useradd -m -s /usr/bin/zsh gd \
 && echo 'gd ALL=(root) NOPASSWD: /usr/bin/mount, /bin/mount' > /etc/sudoers.d/gd \
 && touch /home/gd/.zshrc && chown gd:gd /home/gd/.zshrc

USER gd
WORKDIR /home/gd
ENV TERM=xterm-256color LANG=C.UTF-8 UV_LINK_MODE=copy
