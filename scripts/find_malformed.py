from extractor import PdfExtractor

d=PdfExtractor(r'G:\Book Done.pdf').extract()
for i,p in enumerate(d.pages[:200], start=1):
    for b in p.text_blocks:
        t=b.text.strip()
        if 'Chapter &' in t or 'Chapter S' in t or 'Chapter 1&' in t or 'Chapter 1&' in t:
            print('PAGE', i, 'BLOCK', repr(t))
