import json, re, sys
def az(b):
    m=re.match(r'([NS])\s*(\d+)°\s*(\d+)\'\s*(\d+)"\s*([EW])',b)
    if not m: return None
    ns,dd,mm,ss,ew=m[1],int(m[2]),int(m[3]),int(m[4]),m[5]
    a=dd+mm/60+ss/3600
    return {('N','E'):a,('S','E'):180-a,('S','W'):180+a,('N','W'):360-a}[(ns,ew)]%360
BRG=re.compile(r'[NS]\s*\d{1,2}°\s*\d{1,2}\'\s*\d{1,2}"\s*[EW]')
DIST=re.compile(r"(?<![\d.])\d{1,4}\.\d{2}'")
def match(A,B,tol):
    pool=list(B);h=0
    for a in A:
        bi,bd=None,tol
        for i,g in enumerate(pool):
            d=abs(g-a)
            if d<bd:bd,bi=d,i
        if bi is not None:h+=1;pool.pop(bi)
    return h
blob=open(sys.argv[1],encoding='utf-8').read()
key=json.load(open('_sources/county_test/_key_p0.json'))
key_az=sorted({round(az(b),4) for b in key['bearings_dms'] if az(b) is not None})
key_d=sorted(key['distances_ft'])
read_az=sorted({round(az(b),4) for b in BRG.findall(blob) if az(b) is not None})
read_d=sorted({round(float(x.strip("'")),2) for x in DIST.findall(blob)})
TB,TD=0.02,0.10
rb=match(key_az,read_az,TB);pb=match(read_az,key_az,TB)
rd=match(key_d,read_d,TD);pd=match(read_d,key_d,TD)
lvl=sys.argv[2] if len(sys.argv)>2 else sys.argv[1]
print(f'{lvl:14} | read {len(read_az):3}b/{len(read_d):3}d | BRG recall {rb}/{len(key_az)}={100*rb/len(key_az):3.0f}% prec {100*pb/max(1,len(read_az)):3.0f}% | DIST recall {rd}/{len(key_d)}={100*rd/len(key_d):3.0f}% prec {100*pd/max(1,len(read_d)):3.0f}%')
