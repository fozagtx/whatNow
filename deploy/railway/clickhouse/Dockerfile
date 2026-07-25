FROM clickhouse/clickhouse-server:25.12.5
RUN mkdir -p /opt/user_scripts && cd /tmp \
    && arch="$(uname -m | sed s/aarch64/arm64/ | sed s/x86_64/amd64/)" \
    && wget -q -O hq.tar.gz "https://github.com/SigNoz/signoz/releases/download/histogram-quantile%2Fv0.0.1/histogram-quantile_linux_${arch}.tar.gz" \
    && tar -xzf hq.tar.gz \
    && mv histogram-quantile /opt/user_scripts/histogramQuantile \
    && rm hq.tar.gz
COPY deploy/railway/clickhouse/config.yaml /etc/clickhouse-server/config-0-0.yaml
COPY deploy/railway/clickhouse/functions.yaml /etc/clickhouse-server/functions.yaml
ENV CLICKHOUSE_SKIP_USER_SETUP=1 \
    CLICKHOUSE_CONFIG=/etc/clickhouse-server/config-0-0.yaml
