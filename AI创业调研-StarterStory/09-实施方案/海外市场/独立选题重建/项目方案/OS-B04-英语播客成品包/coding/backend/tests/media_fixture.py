"""Generate a real 30-second 9:16 H.264 + AAC fixture using PyAV."""
import sys
from fractions import Fraction
import av

def generate(path, seconds=30):
    with av.open(str(path),'w',format='mp4') as out:
        video=out.add_stream('libx264',rate=10); video.width=180; video.height=320; video.pix_fmt='yuv420p'; video.options={'preset':'ultrafast','crf':'35'}
        audio=out.add_stream('aac',rate=48000); audio.layout='mono'
        for i in range(round(seconds*10)):
            f=av.VideoFrame(180,320,'yuv420p')
            for p,n in zip(f.planes,[40,128,128]): p.update(bytes([n])*p.buffer_size)
            f.pts=i; f.time_base=Fraction(1,10)
            for pkt in video.encode(f): out.mux(pkt)
            a=av.AudioFrame(format='fltp',layout='mono',samples=4800); a.sample_rate=48000; a.pts=i*4800; a.time_base=Fraction(1,48000)
            for p in a.planes: p.update(bytes(p.buffer_size))
            for pkt in audio.encode(a): out.mux(pkt)
        for stream in (video,audio):
            for pkt in stream.encode(): out.mux(pkt)

if __name__=='__main__': generate(sys.argv[1], float(sys.argv[2]) if len(sys.argv)>2 else 30)
