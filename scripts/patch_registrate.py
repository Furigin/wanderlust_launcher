import struct, zipfile, io, os, sys

def parse_cp(data):
    cnt = struct.unpack('>H', data[8:10])[0]
    i = 10; idx = 1; e = {}
    while idx < cnt:
        tag = data[i]; s = i
        if tag == 1:
            ln = struct.unpack('>H', data[i+1:i+3])[0]
            e[idx] = ('Utf8', data[i+3:i+3+ln], s, i+3+ln); i += 3+ln
        elif tag in (7,8,16,19,20): e[idx]=(tag,data[i+1:i+3],s,i+3); i+=3
        elif tag == 15: e[idx]=(tag,data[i+1:i+4],s,i+4); i+=4
        elif tag in (5,6): e[idx]=(tag,data[i+1:i+9],s,i+9); i+=9; idx+=1
        else: e[idx]=(tag,data[i+1:i+5],s,i+5); i+=5
        idx += 1
    return e, i, cnt

def find_utf8(cp, val):
    for k,v in cp.items():
        if v[0]=='Utf8' and v[1]==val: return k
    return None

def find_class(cp, name_idx):
    for k,v in cp.items():
        if v[0]==7 and struct.unpack('>H', v[1])[0]==name_idx: return k
    return None

def patch(data):
    cp, cp_end, cp_count = parse_cp(data)
    additions = b''
    next_idx = cp_count

    thr_utf8 = find_utf8(cp, b'java/lang/Throwable')
    if thr_utf8 is None:
        thr_utf8 = next_idx; next_idx += 1
        additions += b'\x01' + struct.pack('>H', 19) + b'java/lang/Throwable'
    thr_cls = find_class(cp, thr_utf8) if thr_utf8 < cp_count else None
    if thr_cls is None:
        thr_cls = next_idx; next_idx += 1
        additions += b'\x07' + struct.pack('>H', thr_utf8)
    smt_utf8 = find_utf8(cp, b'StackMapTable')
    if smt_utf8 is None:
        smt_utf8 = next_idx; next_idx += 1
        additions += b'\x01' + struct.pack('>H', 13) + b'StackMapTable'

    code_utf8 = find_utf8(cp, b'Code')
    target_name = find_utf8(cp, b'accept')
    target_desc = find_utf8(cp, b'(Lnet/minecraft/world/item/ItemStack;Lnet/minecraft/world/item/CreativeModeTab$TabVisibility;)V')
    assert target_name and target_desc, "method name/desc not in pool"

    # walk to methods
    p = cp_end + 6
    ifc = struct.unpack('>H', data[p:p+2])[0]; p += 2 + ifc*2
    fcount = struct.unpack('>H', data[p:p+2])[0]; p += 2
    def skip_attrs(p, n):
        for _ in range(n):
            p += 2
            ln = struct.unpack('>I', data[p:p+4])[0]; p += 4 + ln
        return p
    for _ in range(fcount):
        p += 6
        ac = struct.unpack('>H', data[p:p+2])[0]; p += 2
        p = skip_attrs(p, ac)
    mcount = struct.unpack('>H', data[p:p+2])[0]; p += 2
    patched = None
    for _ in range(mcount):
        p += 2
        nidx = struct.unpack('>H', data[p:p+2])[0]; p += 2
        didx = struct.unpack('>H', data[p:p+2])[0]; p += 2
        ac = struct.unpack('>H', data[p:p+2])[0]; p += 2
        for _ in range(ac):
            attr_start = p
            aidx = struct.unpack('>H', data[p:p+2])[0]; p += 2
            ln = struct.unpack('>I', data[p:p+4])[0]; p += 4
            body = p
            if aidx == code_utf8 and nidx == target_name and didx == target_desc:
                old_code_len = struct.unpack('>I', data[body+4:body+8])[0]
                old_code = data[body+8:body+8+old_code_len]
                assert old_code_len == 12 and old_code[-1] == 0xB1, old_code.hex()
                new_code = old_code[:-1] + b'\xa7\x00\x04' + b'\x57' + b'\xb1'
                # 0..10 original, 11: goto +4 -> 15, 14: pop, 15: return
                ex_tab = struct.pack('>H', 1) + struct.pack('>HHHH', 0, 11, 14, thr_cls)
                smt_body = struct.pack('>H', 2) + bytes([78, 7]) + struct.pack('>H', thr_cls) + bytes([0])
                attrs = struct.pack('>H', 1) + struct.pack('>H', smt_utf8) + struct.pack('>I', len(smt_body)) + smt_body
                new_body = struct.pack('>HHI', 3, 3, len(new_code)) + new_code + ex_tab + attrs
                new_attr = struct.pack('>H', aidx) + struct.pack('>I', len(new_body)) + new_body
                patched = (attr_start, body + ln, new_attr)
                break
            p += ln
        if patched: break
    assert patched, "target method not found"
    a, b, new_attr = patched
    out = data[:a] + new_attr + data[b:]
    if additions:
        head = out[:8] + struct.pack('>H', next_idx)
        # constant pool body ends at cp_end (offset in ORIGINAL == same, before methods)
        out = head + out[10:cp_end] + additions + out[cp_end:]
    return out

CREATE = r"C:\Users\rusla\AppData\Roaming\Wanderlust\instances\wanderlust-create\mods\create-1.21.1-6.0.10.jar"
REG_PATH = 'META-INF/jarjar/Registrate-MC1.21-1.3.0+67.jar'
CLS = 'com/tterrag/registrate/util/CreativeModeTabModifier.class'
OUT = sys.argv[1]

zc = zipfile.ZipFile(CREATE)
regdata = zc.read(REG_PATH)
zr = zipfile.ZipFile(io.BytesIO(regdata))
newreg = io.BytesIO()
zo = zipfile.ZipFile(newreg, 'w', zipfile.ZIP_DEFLATED)
for it in zr.infolist():
    buf = zr.read(it.filename)
    if it.filename == CLS:
        buf = patch(buf); print("patched", CLS, len(buf))
    zi = zipfile.ZipInfo(it.filename, date_time=it.date_time)
    zi.compress_type = it.compress_type; zi.external_attr = it.external_attr
    zo.writestr(zi, buf)
zo.close(); zr.close()

zout = zipfile.ZipFile(OUT, 'w', zipfile.ZIP_DEFLATED)
for it in zc.infolist():
    buf = zc.read(it.filename)
    if it.filename == REG_PATH:
        buf = newreg.getvalue()
    zi = zipfile.ZipInfo(it.filename, date_time=it.date_time)
    zi.compress_type = it.compress_type; zi.external_attr = it.external_attr
    zout.writestr(zi, buf)
zout.close(); zc.close()
print("built", OUT, os.path.getsize(OUT))
