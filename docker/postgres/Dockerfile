# Custom PostgreSQL 16 image for Novel LLM.
# Carries zhparser (Chinese full-text segmentation) and pgvector, per ADR-0007.
FROM postgres:16

# The official postgres image ships with the PGDG apt source configured.
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential \
        bzip2 \
        ca-certificates \
        curl \
        git \
        postgresql-server-dev-16 \
        postgresql-16-pgvector \
    && rm -rf /var/lib/apt/lists/*

# SCWS: the Chinese word-segmentation library zhparser is built against.
RUN curl -fsSL https://www.xunsearch.com/scws/down/scws-1.2.3.tar.bz2 -o /tmp/scws.tar.bz2 \
    && mkdir /tmp/scws-src \
    && tar -xjf /tmp/scws.tar.bz2 -C /tmp/scws-src --strip-components=1 \
    && cd /tmp/scws-src \
    && ./configure \
    && make -j"$(nproc)" \
    && make install \
    && ldconfig \
    && rm -rf /tmp/scws.tar.bz2 /tmp/scws-src

# zhparser: PostgreSQL text-search parser on top of SCWS.
RUN git clone --depth 1 https://github.com/amutu/zhparser.git /tmp/zhparser \
    && cd /tmp/zhparser \
    && SCWS_HOME=/usr/local make \
    && make install \
    && rm -rf /tmp/zhparser
