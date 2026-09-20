import av, re, json
from pathlib import Path
from .store import require

def validate(path, kind, filename, clip=None):
    suffix=Path(filename).suffix.lower()
    if kind in ('source','video'):
        require(suffix in (('.mp4',) if kind=='video' else ('.mp4','.mov','.webm')),'Unsupported media extension')
        try:
            with av.open(str(path)) as container:
                videos=list(container.streams.video); audios=list(container.streams.audio)
                require(videos,'A video track is required')
                require(('mp4' in container.format.name or 'mov' in container.format.name) if suffix in ('.mp4','.mov') else 'webm' in container.format.name,'Invalid media container')
                v=videos[0]; duration=float(v.duration*v.time_base) if v.duration else float(container.duration or 0)/av.time_base
                require(duration>0,'Media duration unavailable')
                if kind=='video':
                    require(30-.01<=duration<=90+.01,'Actual video duration must be 30–90 seconds')
                    require(audios,'Video must contain an audio track')
                    require(abs(v.width/v.height-9/16)/(9/16)<=.02,'Video must be 9:16 portrait')
                    require(abs(duration-(clip['end']-clip['start']))<=1,'Video duration must match the approved clip')
                counts={'video':0,'audio':0}
                for packet in container.demux():
                    if packet.stream.type in counts:
                        for frame in packet.decode(): counts[packet.stream.type]+=1
                require(counts['video']>0 and (kind!='video' or counts['audio']>0),'Media tracks must decode successfully')
        except Exception as exc:
            from fastapi import HTTPException
            if isinstance(exc,HTTPException): raise
            require(False,'Media is corrupt or cannot be decoded')
    elif kind=='subtitle':
        require(suffix=='.srt','Subtitle must be an SRT file')
        try: content=Path(path).read_text(encoding='utf-8-sig')
        except UnicodeError: require(False,'Subtitle must be UTF-8')
        blocks=re.split(r'\r?\n\s*\r?\n',content.strip()); previous=0
        def seconds(parts):
            h,m,s,ms=map(int,parts); require(m<60 and s<60,'Invalid SRT timestamp'); return h*3600+m*60+s+ms/1000
        for i,block in enumerate(blocks,1):
            lines=block.splitlines(); require(len(lines)>=3 and lines[0].strip()==str(i),'Invalid SRT sequence or text')
            match=re.fullmatch(r'(\d{2,}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2,}):(\d{2}):(\d{2}),(\d{3})',lines[1].strip())
            require(match is not None,'Invalid SRT timestamp syntax'); start=seconds(match.groups()[:4]); end=seconds(match.groups()[4:])
            require(start>=previous and end>start and end<=clip['end']-clip['start'] and any(x.strip() for x in lines[2:]),'SRT times overlap or exceed clip duration'); previous=end
    elif kind=='project':
        require(suffix in ('.json','.txt'),'Project must be editable JSON or text')
        try:
            content=Path(path).read_text(encoding='utf-8'); require(content.strip(),'Project cannot be empty')
            if suffix=='.json': json.loads(content)
        except (UnicodeError,ValueError): require(False,'Invalid UTF-8 project material')
