import json, re
# --- read blob: concatenation of all 6 blind-subagent raw outputs (page-0 tiles) ---
READ = r"""
R=1372.39' Δ=11°56'37" L=286.08' S42°46'04"E CH=285.56' R=2914.79' Δ=1°04'47" L=54.93' S69°30'32"W CH=54.92'
R=50.00' Δ=127°56'49" L=111.65' N85°32'19"W CH=89.86' R=50.00' Δ=36°17'31" L=31.67' S48°38'02"W CH=31.14'
R=2944.79' Δ=3°14'27" L=166.57' N65°08'18"E CH=166.55' R=50.00' Δ=82°03'25" L=71.61' S75°11'16"E CH=65.64'
S89°58'31"E 1106.07' S89°28'18"E 1117.59' 712.89' 404.70' N44°44'38"W 566.67' N45°17'36"W 572.66'
N41°57'49"W 436.91' N74°48'16"E 172.09'
MEASURED: N0°39'14"E 658.88' R1: N01°09'34"E 656.85' R7: N00°18'47"E 658.99' N0°40'29"E 18.04'
R1: N01°09'34"E 28.63' 404.70' N41°57'49"W 436.91' N20°33'25"E 313.81' N80°04'57"W 64.01' N0°44'19"E 96.63'
N74°48'16"E 172.09' S19°13'13"W 230.39' R1: S18°34'10"W 230.05' S26°13'32"W 144.89' R1: S25°38'54"W 145.07'
C1 1372.39' 11°56'37" 286.08' S42°46'04"E 285.56' C2 2914.79' 1°04'47" 54.93' S69°30'32"W 54.92'
C3 50.00' 127°56'49" 111.65' N85°32'19"W 89.86' C4 50.00' 36°17'31" 31.67' S48°38'02"W 31.14'
C5 2944.79' 3°14'27" 166.57' N65°08'18"E 166.55' C6 50.00' 82°03'25" 71.61' S75°11'16"E 65.64'
(C1) 1372.39' 12°46'25" 305.96' N43°46'27"W 305.33' (C2) 2914.79' 1°04'37" 54.79' N68°51'52"E 54.79'
(C3) 50.00' 127°56'49" 111.65' N86°08'12"W 89.86' (C4) 50.00' 36°17'31" 31.67' S48°02'09"W 31.14'
(C5) 2944.80' 3°14'42" 166.78' S64°33'33"W 166.76' (C6) 50.00' 81°55'40" 71.50' N75°44'44"W 65.56'
C7 1372.39' 3°27'09" 82.69' S47°00'48"E 82.68' C8 1372.39' 8°29'28" 203.39' S41°02'29"E 203.20'
C9 50.00' 20°31'59" 17.92' N40°45'16"E 17.82' C10 50.00' 15°45'32" 13.75' S58°54'02"W 13.71'
?66.67' N39°54'48"W 392.39' (R1: N40°33'34"W 392.60') 436.91' N74°48'16"E 172.09' N45°38'34"E 310.04'
13.52' S56°39'10"W 24.89' (R1: S55°13'06"W 25.00') N34°08'44"W 230.78' (R1: N34°46'54"W 230.77') 217.26'
N80°04'57"W 64.01' N0°44'19"E 96.63' N74°48'16"E 172.09' N45°38'34"E 310.04' N5°22'16"E 211.41'
N19°02'42"W 145.96' S19°13'13"W 230.05' S26°13'32"W 144.89' R1: S25°38'54"W 145.07'
S19°34'38"E 225.35' R1: S20°08'08"E 225.33' 217.26' N34°08'44"W 230.78' R1: N34°46'54"W 230.77'
S63°55'09"W 74.95' R1: S63°17'26"W 74.88' N0°38'32"E 1317.39' R1: N00°38'32"E 1319.61' R7: N00°23'26"E 1317.39'
N89°01'28"W 4.52'
"""
def az(b):
    m=re.match(r'([NS])\s*(\d+)°\s*(\d+)\'\s*(\d+)"\s*([EW])',b)
    ns,dd,mm,ss,ew=m[1],int(m[2]),int(m[3]),int(m[4]),m[5]
    a=dd+mm/60+ss/3600
    return {('N','E'):a,('S','E'):180-a,('S','W'):180+a,('N','W'):360-a}[(ns,ew)]%360
BRG=re.compile(r'[NS]\s*\d{1,2}°\s*\d{1,2}\'\s*\d{1,2}"\s*[EW]')
DIST=re.compile(r"(?<![\d.])\d{1,4}\.\d{2}'")
read_az=sorted({round(az(b),4) for b in BRG.findall(READ)})
read_d =sorted({round(float(x.strip("'")),2) for x in DIST.findall(READ)})
key=json.load(open('_sources/county_test/_key_p0.json'))
key_az=sorted({round(az(b),4) for b in key['bearings_dms']})
key_d =sorted(key['distances_ft'])
def match(A,B,tol):  # count of A found in B (greedy, no reuse)
    pool=list(B); h=0
    for a in A:
        bi,bd=None,tol
        for i,g in enumerate(pool):
            dd=abs(g-a)
            if dd<bd: bd,bi=dd,i
        if bi is not None: h+=1; pool.pop(bi)
    return h
TB,TD=0.02,0.10   # ~1.2 arcmin, 0.1 ft
print(f'KEY (page0 vector text): {len(key_az)} bearings, {len(key_d)} distances')
print(f'READ (blind raster VLM):  {len(read_az)} bearings, {len(read_d)} distances')
rb=match(key_az,read_az,TB); pb=match(read_az,key_az,TB)
rd=match(key_d,read_d,TD);  pd=match(read_d,key_d,TD)
print()
print(f'BEARINGS  recall {rb}/{len(key_az)} = {100*rb/len(key_az):.0f}%   precision {pb}/{len(read_az)} = {100*pb/len(read_az):.0f}%')
print(f'DISTANCES recall {rd}/{len(key_d)} = {100*rd/len(key_d):.0f}%   precision {pd}/{len(read_d)} = {100*pd/len(read_d):.0f}%')
# entropy proof: azimuth spread
import statistics
print(f'\nkey bearing azimuth spread: min {min(key_az):.0f} max {max(key_az):.0f}, distinct {len(set(round(a) for a in key_az))} integer-degree buckets')
