#!/usr/bin/env bash
# Run FalkorDB without Docker or sudo: Debian's redis 8 binary plus the official FalkorDB v6.0.1 module.
set -euo pipefail
D="$HOME/falkor"
mkdir -p "$D/pkg" "$D/data" && cd "$D"
if [ ! -x root/usr/bin/redis-check-rdb ]; then
  curl -sSLo pkg/redis.deb https://deb.debian.org/debian/pool/main/r/redis/redis-tools_8.0.2-3+deb13u3_amd64.deb
  curl -sSLo pkg/lzf.deb https://deb.debian.org/debian/pool/main/libl/liblzf/liblzf1_3.6-4+b4_amd64.deb
  dpkg-deb -x pkg/redis.deb root && dpkg-deb -x pkg/lzf.deb root
  ln -sf redis-check-rdb root/usr/bin/redis-server
fi
if [ ! -f falkordb-x64.so ]; then
  curl -sSLO https://github.com/FalkorDB/FalkorDB/releases/download/v6.0.1/falkordb-x64.so
  chmod +x falkordb-x64.so
fi
export LD_LIBRARY_PATH="$(dirname "$(find "$D/root" -name liblzf.so.1 | head -1)")"
root/usr/bin/redis-server --port 6379 --dir "$D/data" --loadmodule "$D/falkordb-x64.so" --daemonize yes --logfile "$D/redis.log"
echo "FalkorDB on localhost:6379"
