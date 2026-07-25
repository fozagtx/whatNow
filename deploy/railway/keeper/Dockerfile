FROM clickhouse/clickhouse-keeper:25.12.5
COPY deploy/railway/keeper/keeper.yaml /etc/clickhouse-keeper/keeper.yaml
ENTRYPOINT ["/usr/bin/clickhouse-keeper", "--config-file=/etc/clickhouse-keeper/keeper.yaml"]
