"""Stable captions and two scales showing a character's Unicode position."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw

from .unicode_blocks import MAX_CODEPOINT, number_line_position, unicode_block

FOOTER_HEIGHT = 224
BACKGROUND = "#0c1018"
YELLOW = "#f5d76e"
TEAL = "#65d6c4"


class CaptionFooter:
    """Every field has a fixed origin, baseline and monospaced font."""
    def __init__(self, size: tuple[int,int], fonts_root: Path | None = None):
        # Local import avoids a captions <-> video module import cycle.
        from .video import ui_font
        self.size = size
        self.sx, self.sy = size[0]/1280, size[1]/960
        self.height = round(FOOTER_HEIGHT*self.sy)
        self.top = size[1]-self.height
        self.fonts = {key:ui_font(max(1,round(value*self.sx)),fonts_root,monospace=True)
                      for key,value in {"code":36,"name":22,"block":17,"small":14,"axis":11}.items()}

    def point(self, x, y):
        return round(x*self.sx),round(y*self.sy)

    def text(self, draw, text, x, baseline, kind, color, anchor="ls", max_width=None):
        font = self.fonts[kind]
        if max_width is not None:
            limit = max_width*self.sx
            if draw.textlength(text,font=font) > limit:
                while text and draw.textlength(text+"...",font=font) > limit:
                    text = text[:-1]
                text += "..."
        draw.text(self.point(x,baseline),text,font=font,fill=color,anchor=anchor)

    def axis(self, draw, cp, start, end, y, label, *, block=None):
        left,right = 144*self.sx,1244*self.sx
        axis_y = round(y*self.sy)
        self.text(draw,label,36,y+4,"axis","#919aa9")
        draw.line((left,axis_y,right,axis_y),fill="#657187",width=max(1,round(2*self.sy)))
        if block is not None:
            a = number_line_position(block.start,start,end,left,right)
            b = number_line_position(block.end,start,end,left,right)
            draw.line((a,axis_y,b,axis_y),fill=TEAL,width=max(2,round(4*self.sy)))
        # Unicode planes on the whole axis; 16 subdivisions on the block axis.
        ticks = range(0,MAX_CODEPOINT+1,0x10000) if label == "UNICODE" else (
            round(start+(end-start)*i/16) for i in range(17))
        for tick in ticks:
            x = number_line_position(tick,start,end,left,right)
            draw.line((x,axis_y-3*self.sy,x,axis_y+3*self.sy),fill="#657187",width=max(1,round(self.sx)))
        # Fixed endpoint labels never follow the changing marker.
        self.text(draw,f"U+{start:04X}",144,y+21,"axis","#919aa9")
        self.text(draw,f"U+{end:04X}",1244,y+21,"axis","#919aa9",anchor="rs")
        marker = number_line_position(cp,start,end,left,right)
        draw.line((marker,axis_y-6*self.sy,marker,axis_y+5*self.sy),fill=YELLOW,width=max(1,round(2*self.sx)))
        draw.polygon(((marker,axis_y-6*self.sy),(marker-4*self.sx,axis_y-12*self.sy),
                      (marker+4*self.sx,axis_y-12*self.sy)),fill=YELLOW)

    @lru_cache(maxsize=64)
    def static(self, codepoint: int, name: str, font_name: str, mode: str) -> Image.Image:
        image = Image.new("RGB",(self.size[0],self.height),BACKGROUND)
        draw = ImageDraw.Draw(image)
        block = unicode_block(codepoint)
        self.text(draw,f"U+{codepoint:04X}",36,45,"code",YELLOW)
        self.text(draw,block.name if block else "No_Block",292,24,"block",TEAL,max_width=952)
        interval = f"U+{block.start:04X}..U+{block.end:04X}" if block else "No block interval"
        self.text(draw,interval,292,48,"small","#919aa9")
        self.text(draw,name,36,82,"name","#ffffff",max_width=1208)
        self.text(draw,font_name,36,111,"small","#919aa9",max_width=710)
        # The separator and the following fields do not depend on font-name width.
        self.text(draw,"|",770,111,"small","#657187")
        self.text(draw,mode,802,111,"small","#919aa9")
        self.text(draw,"|",920,111,"small","#657187")
        self.axis(draw,codepoint,0,MAX_CODEPOINT,146,"UNICODE",block=block)
        self.axis(draw,codepoint,block.start if block else codepoint,block.end if block else codepoint,193,"BLOCK")
        return image

    def image(self, record: dict, frame_index: int, frame_count: int, mode: str = "stretch") -> Image.Image:
        footer = self.static(record["codepoint"],record["name"],Path(record["font"]).stem,mode).copy()
        draw = ImageDraw.Draw(footer)
        digits = len(str(frame_count))
        self.text(draw,f"frame {frame_index+1:0{digits}d}/{frame_count}",1244,111,"small","#919aa9",anchor="rs")
        return footer

    def close(self):
        self.static.cache_clear()
