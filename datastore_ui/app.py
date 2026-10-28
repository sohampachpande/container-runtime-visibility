"""Streamlit dashboard; run with `python -m streamlit run datastore_ui/app.py`."""

import datetime
import json
import os

import altair as alt
import duckdb
import pandas as pd
import streamlit as st

from datastore_ui.store import Store


st.set_page_config(page_title='Runtime visibility', page_icon='◉', layout='wide')


@st.cache_resource
def store():
    return Store(os.environ.get('STAGING_DIR', '/source/staging'),
                 os.environ.get('DATASTORE_DIR', '/data'))


def frame(sql, parameters=None):
    columns, rows, truncated = store().query(sql, parameters)
    return pd.DataFrame.from_records(rows, columns=columns), truncated


st.title('Runtime visibility')
st.caption('Explore process execution, protected-file checks, and outbound TCP attempts.')

with st.sidebar:
    st.header('Capture')
    st.caption('Import a snapshot of complete events without stopping Vector.')
    if st.button('Import latest', type='primary', use_container_width=True):
        try:
            with st.spinner('Snapshotting and importing events…'):
                results = store().import_latest()
            if results:
                st.success(f"Imported {sum(result['inserted_rows'] for result in results):,} new records.")
            else:
                st.info('No complete staging records yet. Run the security probe, then import again.')
        except (OSError, ValueError, duckdb.Error) as error:
            st.error(f'Import stopped: {error}. Earlier completed batches remain available; retry is safe.')

summary, _ = frame('SELECT count(*) AS records, min(event_time) AS first_event, max(event_time) AS latest_event FROM events')
total = int(summary.iloc[0]['records'])
if total == 0:
    st.info('No imported events yet. Choose Import latest in the sidebar to read Vector’s staging files.')
else:
    st.caption(f"{total:,} stored records · Latest imported event: {summary.iloc[0]['latest_event']} · Times shown in UTC")

view = st.radio('View', ['Explore', 'SQL'], horizontal=True, key='view', label_visibility='collapsed')
if view == 'Explore':
    with st.sidebar:
        st.header('Explore filters')
        window = st.selectbox('Time window', ['Last captured hour', 'Last captured 24 hours', 'All captured data'])
        categories = st.multiselect('Category', ['process', 'file', 'network', 'other'])
        workloads, _ = frame('SELECT DISTINCT container_id_prefix FROM events WHERE container_id_prefix IS NOT NULL ORDER BY 1')
        workload = st.selectbox('Container ID prefix', ['All workloads', *workloads['container_id_prefix'].tolist()])

    conditions = []
    parameters = []
    if total and window != 'All captured data':
        hours = 1 if window == 'Last captured hour' else 24
        conditions.append('event_time >= ?')
        parameters.append(summary.iloc[0]['latest_event'] - datetime.timedelta(hours=hours))
    if categories:
        conditions.append('category IN (' + ','.join('?' for _ in categories) + ')')
        parameters.extend(categories)
    if workload != 'All workloads':
        conditions.append('container_id_prefix = ?')
        parameters.append(workload)
    where = ' WHERE ' + ' AND '.join(conditions) if conditions else ''

    counts, _ = frame('SELECT category, count(*) AS records FROM security_events' + where + ' GROUP BY category', parameters)
    values = dict(zip(counts['category'], counts['records']))
    for column, category in zip(st.columns(4), ['process', 'file', 'network', 'other']):
        column.metric(category.title(), int(values.get(category, 0)))
    st.caption('Counts are exported observations. Permission checks can repeat; TCP attempts do not prove connection success.')

    chart, destinations = st.columns([2, 1])
    with chart:
        st.subheader('Events over time')
        timeline, truncated = frame("SELECT date_trunc('minute', event_time) AS minute, category, count(*) AS records FROM security_events" + where + ' GROUP BY minute, category ORDER BY minute DESC, category', parameters)
        if not timeline.empty:
            timeline['time_utc'] = timeline['minute'].dt.strftime('%Y-%m-%d %H:%M UTC')
            st.altair_chart(alt.Chart(timeline).mark_bar().encode(
                x=alt.X('minute:T', title='Time (UTC)', scale=alt.Scale(type='utc')),
                y=alt.Y('records:Q', title='Records'), color='category:N',
                tooltip=['time_utc:N', 'category:N', 'records:Q']), use_container_width=True)
            if truncated:
                st.caption('Chart limited to the latest 500 time/category groups. Narrow the time window for detail.')
        else:
            st.info('No events match these filters.')
    with destinations:
        st.subheader('Top TCP destinations')
        destination_where = where + (' AND ' if conditions else ' WHERE ') + "category = 'network'"
        top, _ = frame("SELECT coalesce(destination_ip, 'unknown') || ':' || coalesce(CAST(destination_port AS VARCHAR), '?') AS endpoint, count(*) AS attempts FROM security_events" + destination_where + ' GROUP BY endpoint ORDER BY attempts DESC LIMIT 10', parameters)
        if not top.empty:
            st.altair_chart(alt.Chart(top).mark_bar().encode(
                x=alt.X('attempts:Q', title='Attempts', axis=alt.Axis(tickMinStep=1)),
                y=alt.Y('endpoint:N', title=None, sort='-x'),
                tooltip=['endpoint:N', 'attempts:Q']), use_container_width=True)
        else:
            st.caption('No TCP attempts in this selection.')

    st.subheader('Recent observations')
    events, _ = frame('SELECT event_id, event_time, category, executable, container_id_prefix, file_path, permission_mask, permission_result, destination_ip, destination_port, socket_state FROM security_events' + where + ' ORDER BY event_time DESC, event_id LIMIT 200', parameters)
    selection = st.dataframe(events.drop(columns=['event_id']), hide_index=True,
                             on_select='rerun', selection_mode='single-row', key='events', width='stretch')
    st.caption('Latest 200 matching records. Select a row to inspect the complete normalized and original event.')
    if selection.selection.rows:
        index = selection.selection.rows[0]
        if index < len(events):
            detail, _ = frame('SELECT normalized_event FROM events WHERE event_id = ?', [events.iloc[index]['event_id']])
            st.json(json.loads(detail.iloc[0]['normalized_event']))

if view == 'SQL':
    st.subheader('Query captured events')
    st.caption('One read-only SELECT · Up to 500 rows · 5-second timeout · File access disabled. Explore filters do not apply here.')
    with st.expander('Available fields'):
        schema, _ = frame("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'security_events' ORDER BY ordinal_position")
        st.dataframe(schema, hide_index=True, width='stretch')
        st.code("SELECT normalized_event->'tetragon' AS original_event FROM security_events LIMIT 1", language='sql')
    with st.form('sql_query'):
        sql = st.text_area('SQL', value='SELECT category, count(*) AS records\nFROM security_events\nGROUP BY category\nORDER BY records DESC', height=150)
        submitted = st.form_submit_button('Run query', type='primary')
    if submitted:
        try:
            result, truncated = frame(sql)
            st.dataframe(result, hide_index=True, width='stretch')
            st.caption(f'{len(result):,} rows shown' + ('; result limited to 500 rows.' if truncated else '.'))
        except (ValueError, duckdb.Error) as error:
            st.error(f'Query failed: {error}')
