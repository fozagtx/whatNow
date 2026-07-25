FROM curlimages/curl:latest
# Real HTTP requests against the running HotROD app, forever.
CMD ["sh", "-c", "while true; do for c in 123 392 731 567; do \
    curl -s -o /dev/null --max-time 10 \"${HOTROD_URL}/dispatch?customer=${c}&nonse=$(date +%s)${c}\"; \
    done; sleep 25; done"]
