"""A session owns one workspace. Narrative files remain agent/user maintained."""
from datetime import datetime
import hashlib
import html
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import quote

MARKER = '.codex-session-mirror.json'
OWNER = 'codex-session-mirror-v1'
HISTORY = '历史记录.jsonl'
NOTES = '会话说明.md'
STATUS_START = '<!-- codex-session-mirror:status:start -->'
STATUS_END = '<!-- codex-session-mirror:status:end -->'
NAV_START = '<!-- codex-session-mirror:sessions:start -->'
NAV_END = '<!-- codex-session-mirror:sessions:end -->'
NOTES_BODY = '''# 会话说明

## 目标与当前进度

待 Agent 根据本 session 的实际工作补充。

## 已确认的决定

待补充。

## 文件入口

- [工作索引](工作/索引.md)：Agent 生成内容及当前推荐版本。
- 资料由用户控制；Agent 默认只读，加工副本放工作区。
- 成果由用户选择保留；未经明确授权，Agent 不往成果区放入或修改文件。

## 下一步

待补充。
'''
WORK_INDEX = '''# 工作索引

按有明确目的的一批工作分组，同一工作项跨天继续复用目录。

| 工作项 | 用途 | 当前推荐文件 | 状态 |
| --- | --- | --- | --- |

Agent 维护此索引；“已采用/未采用”依据用户决定标注。临时文件和验证输出放所属工作组内部，清理前检查引用并取得用户授权。
'''


def plain_dir(path, create=False):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Expected absolute directory without traversal')
    for p in [*reversed(path.parents), path]:
        if p.is_symlink():
            raise ValueError('Refusing symlink directory: ' + str(p))
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.is_dir():
        raise FileNotFoundError(str(path))
    return path


def project_root(cwd):
    cwd = plain_dir(cwd)
    # A tool may be invoked inside this session's code/work directory.
    for p in [cwd, *cwd.parents]:
        marker = p / MARKER
        if marker.is_file() and not marker.is_symlink():
            data = json.loads(marker.read_text(encoding='utf-8'))
            if data.get('owner') == OWNER and data.get('layout_version') == 3:
                root = plain_dir(data['project_directory'])
                if root not in cwd.parents and root != cwd:
                    raise ValueError('Workspace project does not contain cwd')
                return root
    for p in [cwd, *cwd.parents]:
        if (p / '.git').exists() or (p / '项目导航.md').is_file() or (p / 'codex-file').is_dir():
            return p
    return cwd


def atomic_text(path, text):
    path = Path(path)
    if path.is_symlink():
        raise ValueError('Refusing symlink document')
    if path.exists() and path.read_text(encoding='utf-8') == text:
        return
    fd, temp = tempfile.mkstemp(prefix='.mirror-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def create_text(path, text):
    if path.is_symlink():
        raise ValueError('Refusing symlink document')
    try:
        with path.open('x', encoding='utf-8') as f:
            f.write(text)
    except FileExistsError:
        pass


def initialize(directory):
    directory = plain_dir(directory)
    for part in ['资料', '工作', '成果']:
        plain_dir(directory / part, create=True)
    create_text(directory / NOTES, NOTES_BODY)
    create_text(directory / '工作/索引.md', WORK_INDEX)


def link(label, path):
    label = ' '.join(str(label).splitlines()).replace('\\', '\\\\').replace('[', '\\[').replace(']', '\\]').replace('|', '\\|')
    return '[' + label + '](' + quote(str(path), safe='/.-_') + ')'


def replace_block(original, start, end, block):
    if start in original and end in original:
        a, b = original.index(start), original.index(end)
        if b < a or original.count(start) != 1 or original.count(end) != 1:
            raise ValueError('Malformed generated document block')
        return original[:a] + block + original[b + len(end):]
    if start in original or end in original:
        raise ValueError('Incomplete generated document block')
    return original.rstrip() + '\n\n' + block + '\n'


def plain_label(value):
    text = html.escape(' '.join(str(value).splitlines()))
    return re.sub(r'([\\`*_{}\[\]()#!|])', r'\\\1', text)


def fork_status(entry):
    parent = entry.get('forked_from_id')
    if not parent:
        return ''
    line = '- 分叉来源 ID：`' + str(parent) + '`'
    if entry.get('fork_state') == 'complete':
        when = datetime.fromtimestamp(entry['fork_snapshot_at']).strftime('%Y-%m-%d %H:%M:%S')
        line += '；已一次性复制父工作区，之后独立维护。\n- 工作区复制时间：' + when
    else:
        line += '；既有工作区保留，未追溯补拷父文件。'
    if entry.get('fork_warnings'):
        line += '\n- 引用检查：以下文件可能仍指向父目录，未自动改写：' + '、'.join(plain_label(p) for p in entry['fork_warnings'])
    return line + '\n'


def update_status(entry):
    directory = plain_dir(entry['directory'])
    doc = directory / NOTES
    if doc.is_symlink():
        raise ValueError('Refusing symlink session notes')
    original = doc.read_text(encoding='utf-8')
    state = '已归档' if entry.get('archived') else '活跃'
    if entry.get('status') == 'source_deleted':
        state = '源会话已删除，文件保留'
    elif entry.get('status') != 'synced':
        state += '（同步待重试）'
    when = datetime.fromtimestamp(entry['last_synced_at']).strftime('%Y-%m-%d %H:%M:%S') if entry.get('last_synced_at') else '尚未同步'
    history = link('原始历史记录', HISTORY) if (directory / HISTORY).is_file() else '原始历史记录已移除或尚未同步'
    name = ' '.join(str(entry.get('name', entry['id'])).splitlines())
    block = (STATUS_START + '\n\n## 同步信息（自动维护）\n\n'
             + f'- 会话：{name}\n- 会话 ID：`{entry["id"]}`\n- 状态：{state}\n- 最后同步：{when}\n- {history}\n'
             + fork_status(entry)
             + '\n' + STATUS_END)
    atomic_text(doc, replace_block(original, STATUS_START, STATUS_END, block))


def empty_scaffold(directory):
    """True only if no user/agent work has been added to the initial skeleton."""
    directory = plain_dir(directory)
    allowed = {MARKER, NOTES, '资料', '工作', '成果'}
    if any(p.name not in allowed or p.is_symlink() for p in directory.iterdir()):
        return False
    doc = directory / NOTES
    if doc.exists():
        body = doc.read_text(encoding='utf-8')
        if STATUS_START in body or STATUS_END in body:
            if body.count(STATUS_START) != 1 or body.count(STATUS_END) != 1:
                return False
            start, end = body.index(STATUS_START), body.index(STATUS_END)
            if end < start:
                return False
            body = body[:start] + body[end + len(STATUS_END):]
        body = body.strip()
        if body != NOTES_BODY.strip():
            return False
    for area in ['资料', '成果']:
        if (directory / area).exists() and any((directory / area).iterdir()):
            return False
    work = directory / '工作'
    if work.exists():
        for p in work.iterdir():
            if p.is_symlink() or p.name != '索引.md' or not p.is_file() or p.read_text(encoding='utf-8') != WORK_INDEX:
                return False
    return True


def remove_empty_scaffold(directory):
    if not empty_scaffold(directory):
        return False
    for file in [directory / NOTES, directory / '工作/索引.md', directory / MARKER]:
        file.unlink(missing_ok=True)
    for area in ['资料', '工作', '成果']:
        if (directory / area).exists():
            (directory / area).rmdir()
    directory.rmdir()
    return True


def update_project_index(project, entries, scan_error=None):
    project = plain_dir(project)
    path = project / '项目导航.md'
    if path.is_symlink():
        raise ValueError('Refusing symlink project navigation')
    original = path.read_text(encoding='utf-8') if path.exists() else '# 项目导航\n\n按 session 查找上下文、资料和生成内容。\n'
    # Replace only the prior version's machine-owned task list, never human prose.
    original = re.sub(r'<!-- codex-session-mirror:tasks:start -->.*?<!-- codex-session-mirror:tasks:end -->', '', original, flags=re.S)
    rows = []
    issues = []
    if scan_error:
        issues.append('- **全局同步异常**：' + plain_label(scan_error) + '。先检查同步服务，当前导航可能不是最新状态。')
    for sid, entry in sorted(entries, key=lambda pair: pair[1].get('last_synced_at', 0), reverse=True):
        if entry.get('error') or entry.get('fork_job'):
            label = '分叉复制失败或待恢复' if entry.get('fork_state') == 'pending' or entry.get('fork_job') else '同步失败'
            issues.append('- **' + label + '**：' + plain_label(entry.get('name', sid))
                          + '（ID：`' + sid + '`）— ' + plain_label(entry.get('error', '复制事务尚未完成'))
                          + '。保留已有文件，先排查原因；不要创建同名空目录或覆盖重试。')
        if entry.get('fork_warnings'):
            issues.append('- **分叉引用待检查**：' + plain_label(entry.get('name', sid)) + ' — '
                          + '、'.join(plain_label(p) for p in entry['fork_warnings']) + '。文件已复制，绝对路径或 Git 工作树引用未自动改写。')
        directory = Path(entry.get('directory', '/nonexistent'))
        archive = Path(entry['archive_path']) if entry.get('archive_path') else None
        if entry.get('layout_version') != 3 or (not directory.is_dir() and not (archive and archive.is_file())):
            continue
        state = '已归档' if entry.get('archived') else '活跃'
        if entry.get('status') == 'source_deleted':
            state = '源会话已删除，文件保留'
        if entry.get('status') == 'retry':
            state += '（同步待重试）'
        target = archive if archive and archive.is_file() else directory / NOTES
        if not target.exists():
            continue
        rows.append('- ' + link(entry.get('name', sid), target.relative_to(project)) + ' — ' + state)
    block = NAV_START + '\n\n## 同步异常与待处理\n\n' + ('\n'.join(issues) or '当前没有已记录的同步异常。') + '\n\n## 会话入口\n\n' + ('\n'.join(rows) or '暂无已同步的会话。') + '\n\n' + NAV_END
    atomic_text(path, replace_block(original, NAV_START, NAV_END, block))
