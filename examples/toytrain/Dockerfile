# Copyright (C) 2026 Laurent Millet-Lacombe
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# This file is part of testsuit.
# testsuit is free software: you can redistribute it and/or modify it under the terms of the GNU Affero
# General Public License as published by the Free Software Foundation, either version 3 of the License,
# or (at your option) any later version.
# testsuit is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; without even the
# implied warranty of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Affero General
# Public License for more details.
# You should have received a copy of the GNU Affero General Public License along with testsuit. If not,
# see <https://www.gnu.org/licenses/>.

# Image of the toy-train test environment: testsuit with its sequencer, and the toytrain package.
# By default it runs the sequencer of the emulated mode (tsq.dev.yml), with its UI and Editor on port 8640.
# Build and run it with docker-compose.yml, from this folder: docker compose up --build

FROM debian:12.14-slim

LABEL APPNAME="toytrain"
LABEL PRODUCT="sample_product"

ARG CONDA_VERSION=Miniconda3-py313_26.3.2-2
ENV CONDA_VERSION=${CONDA_VERSION}
ENV ANACONDA3_PATH=/opt/apps/anaconda3
ENV PATH=$ANACONDA3_PATH/bin:$PATH

# proxies of the build host, if any (see .env.sample); apt falls back to CI_HTTPS_PROXY for http
ARG CI_HTTP_PROXY
ENV CI_HTTP_PROXY=$CI_HTTP_PROXY
ARG CI_HTTPS_PROXY
ENV CI_HTTPS_PROXY=$CI_HTTPS_PROXY

# testsuit as pip installs it: a PyPI requirement, or "testsuit[sequencer] @ git+https://..." for a branch
ARG TESTSUIT_SPEC="testsuit[sequencer]>=0.4.6"

WORKDIR /app

# git: the Editor and the datapack tool; openssh-client: the sequencer's ssh transport; iputils-ping: probes
RUN http_proxy=${CI_HTTP_PROXY:-$CI_HTTPS_PROXY} https_proxy=${CI_HTTPS_PROXY} apt-get update \
    && http_proxy=${CI_HTTP_PROXY:-$CI_HTTPS_PROXY} https_proxy=${CI_HTTPS_PROXY} apt-get install -y --no-install-recommends \
        curl \
        wget \
        iputils-ping \
        openssh-client \
        git \
        bzip2 \
        ca-certificates \
        libglib2.0-0 \
        libxext6 \
        libxrender1 \
        libsm6 \
        libfontconfig1 \
        vim \
        zip \
    && rm -rf /var/lib/apt/lists/*

# Miniconda (x86_64: the sequencer host)
RUN https_proxy=${CI_HTTPS_PROXY} wget --progress=bar:force:noscroll \
        https://repo.anaconda.com/miniconda/${CONDA_VERSION}-Linux-x86_64.sh -O /tmp/conda.sh \
    && bash /tmp/conda.sh -b -p $ANACONDA3_PATH \
    && rm /tmp/conda.sh

# testsuit and its sequencer first: this layer is kept while only the toy-train files change
RUN python -m pip install --no-cache-dir --proxy="${CI_HTTPS_PROXY}" "${TESTSUIT_SPEC}" pytest

# the toy-train package (its testsuit plugin and the toytrain command), then the rest of the example
COPY pyproject.toml README.md /app/
COPY src/ /app/src/
RUN python -m pip install --no-cache-dir --proxy="${CI_HTTPS_PROXY}" "setuptools>=69" \
    && python -m pip install --no-cache-dir --proxy="${CI_HTTPS_PROXY}" -e /app
COPY . /app

# the container runs as the host user (docker-compose.yml: user), unknown to the image: give it a name (getpass,
# git) and a home writable by any uid, holding the sequencer's storage (tsq.dev.yml: ~/.toytrain-emu)
ENV HOME=/home/toytrain USER=toytrain LOGNAME=toytrain
RUN mkdir -p $HOME/.toytrain-emu && chmod -R 1777 $HOME \
    && git config --system --add safe.directory '*'

# the sequencer's UI and REST API (tsq.dev.yml: server.listen, opened to the host by --listen below)
EXPOSE 8640

ENTRYPOINT ["tsqd"]
CMD ["--config", "tsq.dev.yml", "--listen", "0.0.0.0:8640"]
