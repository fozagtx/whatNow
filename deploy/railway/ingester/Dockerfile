FROM signoz/signoz-otel-collector:latest
COPY deploy/railway/ingester/ingester.yaml /etc/otel-collector-config.yaml
COPY deploy/railway/ingester/opamp.yaml /etc/opamp-config.yaml
ENTRYPOINT ["/bin/sh", "-c", "\
    /signoz-otel-collector migrate ready && \
    /signoz-otel-collector migrate bootstrap && \
    /signoz-otel-collector migrate sync up && \
    /signoz-otel-collector migrate async up && \
    exec /signoz-otel-collector --config=/etc/otel-collector-config.yaml --manager-config=/etc/opamp-config.yaml --copy-path=/var/tmp/collector-config.yaml"]
