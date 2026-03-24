aws lambda invoke \
  --function-name failure-analysis-report \
  --cli-read-timeout 900 \
  /tmp/far-test-output.json && cat /tmp/far-test-output.json
