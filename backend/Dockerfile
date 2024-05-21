FROM python:3.11.7-slim-bookworm

LABEL com.danswer.maintainer="founders@danswer.ai"
LABEL com.danswer.description="This image is for the backend of Danswer. It is MIT Licensed and \
free for all to use. You can find it at https://hub.docker.com/r/danswer/danswer-backend. For \
more details, visit https://github.com/danswer-ai/danswer."


# Default DANSWER_VERSION, typically overriden during builds by GitHub Actions.
ARG DANSWER_VERSION=0.3-dev
ENV DANSWER_VERSION=${DANSWER_VERSION}
RUN echo "DANSWER_VERSION: ${DANSWER_VERSION}"

# 定义构建参数
ARG DEBIAN_MIRROR=http://deb.debian.org
ARG PIP_INDEX_URL=https://pypi.org/simple
ARG PIP_TRUSTED_HOST=pypi.org
ARG HF_ENDPOINT=https://huggingface.co

# 确保 /etc/apt/sources.list 文件存在并替换镜像源
RUN echo "deb ${DEBIAN_MIRROR}/debian/ bookworm main contrib non-free" > /etc/apt/sources.list && \
    echo "deb ${DEBIAN_MIRROR}/debian/ bookworm-updates main contrib non-free" >> /etc/apt/sources.list && \
    echo "deb ${DEBIAN_MIRROR}/debian-security bookworm-security main contrib non-free" >> /etc/apt/sources.list

ENV PIP_INDEX_URL=${PIP_INDEX_URL} \
    PIP_TRUSTED_HOST=${PIP_TRUSTED_HOST} \
    HF_ENDPOINT=${HF_ENDPOINT}
# Install system dependencies
# cmake needed for psycopg (postgres)
# libpq-dev needed for psycopg (postgres)
# curl included just for users' convenience
# zip for Vespa step futher down
# ca-certificates for HTTPS
RUN apt-get update && \
    apt-get install -y cmake curl zip ca-certificates libgnutls30=3.7.9-2+deb12u2 \
    libblkid1=2.38.1-5+deb12u1 libmount1=2.38.1-5+deb12u1 libsmartcols1=2.38.1-5+deb12u1 \
    libuuid1=2.38.1-5+deb12u1 && \
    rm -rf /var/lib/apt/lists/* && \
    apt-get clean

# Install Python dependencies
# Remove py which is pulled in by retry, py is not needed and is a CVE
COPY ./requirements/default.txt /tmp/requirements.txt
# 分步执行每个命令并增加调试信息
RUN pip install --no-cache-dir --upgrade -r /tmp/requirements.txt --default-timeout=100 || { echo 'pip install failed'; exit 1; }
RUN pip uninstall -y py || { echo 'pip uninstall failed'; exit 1; }
RUN playwright install chromium || { echo 'playwright install failed'; exit 1; }
RUN playwright install-deps chromium || { echo 'playwright install-deps failed'; exit 1; }
RUN ln -s /usr/local/bin/supervisord /usr/bin/supervisord || { echo 'ln command failed'; exit 1; }
# RUN pip install --no-cache-dir --upgrade -r /tmp/requirements.txt --default-timeout=100 && \
#     pip uninstall -y py && \
#     playwright install chromium && playwright install-deps chromium && \
#     ln -s /usr/local/bin/supervisord /usr/bin/supervisord

# Cleanup for CVEs and size reduction
# https://github.com/tornadoweb/tornado/issues/3107
# xserver-common and xvfb included by playwright installation but not needed after
# perl-base is part of the base Python Debian image but not needed for Danswer functionality
# perl-base could only be removed with --allow-remove-essential
RUN apt-get remove -y --allow-remove-essential perl-base xserver-common xvfb cmake \
    libldap-2.5-0 libldap-2.5-0 && \
    apt-get autoremove -y && \
    rm -rf /var/lib/apt/lists/* && \
    rm /usr/local/lib/python3.11/site-packages/tornado/test/test.key

# Pre-downloading models for setups with limited egress
RUN python -c "from transformers import AutoTokenizer; AutoTokenizer.from_pretrained('intfloat/e5-base-v2')"

# Pre-downloading NLTK for setups with limited egress
RUN python -c "import nltk; \
nltk.download('stopwords', quiet=True); \
nltk.download('wordnet', quiet=True); \
nltk.download('punkt', quiet=True);"

# Set up application files
WORKDIR /app
COPY ./danswer /app/danswer
COPY ./shared_configs /app/shared_configs
COPY ./alembic /app/alembic
COPY ./alembic.ini /app/alembic.ini
COPY supervisord.conf /usr/etc/supervisord.conf

ENV PYTHONPATH /app

# Default command which does nothing
# This container is used by api server and background which specify their own CMD
CMD ["tail", "-f", "/dev/null"]
