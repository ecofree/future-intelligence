-- Quiescent restore drill only. Hashes rather than raw research rows in output.
SELECT format('SELECT %L || ''|'' || count(*)::text || ''|'' || md5(coalesce(string_agg(row_to_json(t)::text, E''\n'' ORDER BY row_to_json(t)::text), '''')) FROM %I.%I t;',
              schemaname||'.'||tablename,schemaname,tablename)
FROM pg_tables WHERE schemaname IN ('fi','fi_meta') ORDER BY schemaname,tablename
\gexec
