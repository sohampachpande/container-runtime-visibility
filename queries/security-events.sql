-- Run after the batch importer closes. Bounds are relative to the capture's
-- latest event, so saved captures remain useful after the wall clock advances.

-- Coverage in the last captured hour, grouped by exported workload identity.
SELECT
    normalized_event->>'category' AS category,
    container_id_prefix,
    count(*) AS records
FROM events
WHERE event_time >= (SELECT max(event_time) - INTERVAL '1 hour' FROM events)
GROUP BY category, container_id_prefix
ORDER BY category, records DESC;

-- Execution history. A Docker ID prefix is not a full ID or a cgroup identity.
SELECT event_time, container_id_prefix, process_exec_id,
       normalized_event->'process'->>'binary' AS executable,
       normalized_event->'process'->>'arguments' AS arguments
FROM events
WHERE event_type = 'process_exec'
  AND event_time >= (SELECT max(event_time) - INTERVAL '1 hour' FROM events)
ORDER BY event_time DESC
LIMIT 100;

-- Permission-hook checks: mask 4 is read, mask 2 is write; result 0 means the
-- permission hook allowed the operation. It does not prove a completed read.
SELECT event_time, container_id_prefix, process_exec_id,
       normalized_event->'file'->>'path' AS path,
       normalized_event->'file'->>'permission_mask' AS permission_mask,
       normalized_event->'file'->>'permission_result' AS permission_result
FROM events
WHERE (normalized_event->>'category') = 'file'
  AND event_time >= (SELECT max(event_time) - INTERVAL '1 hour' FROM events)
ORDER BY event_time DESC
LIMIT 100;

-- Outbound attempts observed at tcp_connect, not established sessions.
SELECT event_time, container_id_prefix, process_exec_id,
       normalized_event->'network'->>'destination_ip' AS destination_ip,
       normalized_event->'network'->>'destination_port' AS destination_port,
       normalized_event->'network'->>'state' AS socket_state
FROM events
WHERE (normalized_event->>'category') = 'network'
  AND event_time >= (SELECT max(event_time) - INTERVAL '1 hour' FROM events)
ORDER BY event_time DESC
LIMIT 100;
