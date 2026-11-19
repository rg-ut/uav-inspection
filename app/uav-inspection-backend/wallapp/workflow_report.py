"""PDF report using the same finding counts as the inspection workbench."""
from PIL import Image, ImageDraw, ImageOps

from wallapp.report import _font, _paragraph, PAGE_SIZE, MARGIN
from wallapp.workflow import KIND_LABELS, ROOT, now


def generate(path, task, counts, findings, runs):
    pages = []

    def page(title):
        image = Image.new('RGB', PAGE_SIZE, 'white')
        pages.append(image)
        draw = ImageDraw.Draw(image)
        draw.text((MARGIN, MARGIN), title, fill='#0f172a', font=_font(44, True))
        return draw

    width = PAGE_SIZE[0] - MARGIN * 2
    draw = page('建筑外墙巡检报告')
    run = runs[0] if runs else None
    y = MARGIN + 88
    lines = [
        f"任务　{task['name']}",
        f"巡检位置　{task['building']} · {task['facade']}",
        f"任务编号　{task['id']}",
        f"生成时间　{now()}",
        f"疑似待复核 {counts['pending']} 处　已确认缺陷 {counts['confirmed']} 处",
        f"已派单 {counts['assigned']} 处　已修复 {counts['completed']} 处",
    ]
    if run:
        state = {'queued': '等待分析', 'running': '正在分析', 'failed': '分析未完成',
                 'completed': '分析完成', 'cancelled': '分析已停止'}[run['state']]
        lines += [f"最近分析　{state}，已分析 {run['coverage']:.1f} 秒",
                  f"模型版本　{run['model']}"]
    else:
        lines.append('还未检测')
    for text in lines:
        y = _paragraph(draw, (MARGIN, y), text, _font(28), '#334155', width, 15) + 10

    samples = [f for f in findings if f['evidence'] and f['state'] in {'pending', 'confirmed'}][:12]
    for finding in samples:
        source = ROOT / 'evidence' / task['id'] / finding['evidence']
        if not source.is_file():
            continue
        draw = page('巡检发现')
        with Image.open(source) as original:
            screenshot = ImageOps.contain(original.convert('RGB'), (width, 900))
            pages[-1].paste(screenshot, (MARGIN, MARGIN + 85))
        y = MARGIN + 115 + screenshot.height
        state = '已确认' if finding['state'] == 'confirmed' else '疑似待复核'
        kind = KIND_LABELS.get(finding['kind'], '面砖脱落')
        location = ' · '.join(filter(None, [finding['floor'] or '楼层待核实', finding['zone']]))
        y = _paragraph(draw, (MARGIN, y), f"{kind}　{state}　视频 {finding['evidence_time']:.1f} 秒",
                       _font(30, True), '#0f172a', width)
        y = _paragraph(draw, (MARGIN, y + 12), location, _font(26), '#334155', width)
        _paragraph(draw, (MARGIN, y + 12), finding['note'][:350], _font(24), '#334155', width)
    pages[0].save(path, 'PDF', resolution=150, save_all=True, append_images=pages[1:])
