# Dahlia migration helper: runs as a privileged container on Unraid and
# installs Dahlia Linux onto a free disk. Build context: repository root.
#   docker build -f migrate/Dockerfile -t migrate .
FROM debian:trixie-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        debootstrap gdisk parted dosfstools btrfs-progs util-linux udev ethtool \
        python3 ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY packages/archive-keyring/usr/share/keyrings/dahlia-archive-keyring.asc /usr/share/keyrings/
COPY migrate/install.sh /opt/migrate/install.sh
COPY migrate/helper /opt/migrate/helper
COPY migrate/www /opt/migrate/www

WORKDIR /opt/migrate
EXPOSE 8484
# -E -s instead of -I: -I would drop the working directory, where helper/ is.
CMD ["python3", "-E", "-s", "-m", "helper.web"]
