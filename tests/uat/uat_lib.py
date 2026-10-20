import json, math, subprocess, os, sys

CORE = os.path.join(os.path.dirname(os.path.abspath(__file__)), '../../core/build/ktv_core')
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'runs')
os.makedirs(OUT, exist_ok=True)
O = (10.7769, 106.7009)

KINDS = {  # key: group, group_id, type_id, type_name, sla, priority, route status (id, name)
    'net': ('trien_khai', 1, 3, 'trien_khai_net', 120, 3, (97, 'Đã nhận tuyến')),
    'swap': ('trien_khai', 1, 5, 'swap', 60, 3, (97, 'Đã nhận tuyến')),
    'gsafe': ('trien_khai', 1, 7, 'gsafe', None, 3, (97, 'Đã nhận tuyến')),
    'cam': ('trien_khai', 1, 6, 'giao_thiet_bi_cam', 60, 3, (97, 'Đã nhận tuyến')),
    'vatly': ('bao_tri', 2, 1, 'bao_tri_vat_ly', 60, 1, (6, 'Đã nhận ca')),
    'logic': ('bao_tri', 2, 2, 'bao_tri_logic', 60, 2, (6, 'Đã nhận ca')),
    'thuhoi': ('thu_hoi', 3, 1, 'thu_hoi_thiet_bi', None, 4, (0, 'Chưa thu hồi')),
    'bill': ('hoa_don', 4, 1, 'tra_sau', None, 4, (0, 'Chưa thanh toán')),
}


def pt(x, y):  # km đông, km bắc từ O
    return (O[0] + y / 111.195, O[1] + x / (111.195 * math.cos(math.radians(O[0]))))


def ll(p):
    return f'{p[0]:.6f},{p[1]:.6f}'


def T(tid, name, kind, xy, appt='', sla=None, handle=0, addr=None, complete_date='', status=None, create=None):
    g, gid, tyid, tyname, dsla, prio, st = KINDS[kind]
    st = status or st
    return {'_name': name, 'task_id': tid, 'task_group_id': gid, 'task_group_name': g, 'task_type_id': tyid,
            'task_type_name': tyname, 'task_sub_id': 0, 'task_sub_name': '', 'task_status_id': st[0],
            'task_status_name': st[1], 'sla': {'sla_minutes': sla if sla is not None else dsla, 'priority_in_day': prio},
            'appointment': appt, 'create_date': create or '', 'complete_date': complete_date,
            'location': addr or f'Địa chỉ {name}', 'latlng': ll(pt(*xy)), 'handle_minutes': handle,
            'task_plots_id': 7, 'staff_plots_id': 7, 'staff_role': 1, 'block_id': 302916}


def run(case, at, tasks, staff_xy=(0, 0), available='08:00-17:30', mode=0, current=None, staff='00201964',
        latlng=None, quiet=False):
    names = {t['task_id']: t['_name'] for t in tasks}
    groups = {}
    for t in tasks:
        t = dict(t); t.pop('_name')
        if not t['create_date']: t['create_date'] = at[:10] + ' 06:00:00'
        groups.setdefault(t['task_group_name'], []).append(t)
    msg = {'message_id': f'uat-{case}', 'planned_at': at, 'trigger': 'TASK_CHANGE', 'priority_type': mode,
           'staff': {'staff_id': staff, 'staff_account': 'UAT.KTV', 'latlng': ll(pt(*staff_xy)) if latlng is None else latlng,
                     'available': available, 'plots': [{'id': 7, 'name': 'Phú Mỹ', 'role': 1, 'block_id': 302916}],
                     'status': 1, 'current_task': current},
           'tasks': groups}
    if latlng == 'DROP': msg['staff'].pop('latlng')
    fin, fout = f'{OUT}/{case}.in.json', f'{OUT}/{case}.out.json'
    json.dump(msg, open(fin, 'w'), ensure_ascii=False, indent=1)
    if os.path.exists(fout): os.remove(fout)
    subprocess.run([CORE, 'plan', fin, '--at', at, '--out', fout], capture_output=True)
    out = json.loads(open(fout).readline())
    rows = [r for c in out['data'][0]['clusters'] for r in c['schedule']] if out.get('data') else []
    lines = [f"  [{case}] statuscode {out['statuscode']} {out.get('message','')}".rstrip()]
    for r in rows:
        if r['entry_type'] == 'TASK' and r['seq'] == 0:
            lines.append(f"   ĐANG LÀM (seq 0) {names.get(r['task_id'], r['task_id'])} dự kiến xong {r['end_at'][11:16]}")
        elif r['entry_type'] == 'TASK':
            nm = names.get(r['task_id'], r['task_id'])
            lines.append(f"   #{r['seq']} {r['at']}-{r['end_at'][11:16]} {nm} ({r['task_type_name']}"
                         f"{', hẹn ' + r['appointment'][11:16] if r['appointment'] else ''}) đi {r['travel_minutes_before']}′/"
                         f"{r['travel_km_before']}km role={r['task_role']}{'/' + r['insert_reason'] if r['insert_reason'] else ''}"
                         f" sla={r['projected_sla']}")
        elif r['entry_type'] == 'DEFERRED':
            lines.append(f"   DEFERRED {names.get(r['task_id'], r['task_id'])} ({r['task_type_name']}) lý do={r['insert_reason']}")
        else:
            lines.append(f"   · {r['entry_type']} {r['at']}-{r['end_at'][11:16]} {r.get('label','')}")
    if out.get('data'):
        m = out['data'][0].get('metrics', {})
        lines.append('   metrics: ' + json.dumps(m, ensure_ascii=False))
    for w in out.get('warnings', []) or []:
        lines.append(f"   warn: {w}")
    print('\n'.join(lines))
    return out, rows, names


def order(rows, names):
    return [names.get(r['task_id']) for r in rows if r['entry_type'] == 'TASK']
