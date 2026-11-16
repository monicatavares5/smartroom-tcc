from pathlib import Path
import re

p = Path(r'C:\TCC\SmartRoom\esp-csi\examples\get-started\smartroom_node\main\app_main.c')
if not p.exists():
    raise SystemExit(f'Arquivo não encontrado: {p}')
s = p.read_text(encoding='utf-8-sig')
if 'SMARTROOM_CSI_V2' in s:
    raise SystemExit('Patch V2 já aplicado. Nada foi alterado.')
if '/* SMARTROOM_UDP_PATCH_V1 */' not in s:
    raise SystemExit('Patch V1 não encontrado. Nada foi alterado.')
backup = p.with_name('app_main_pre_csi_v2.bak')
backup.write_bytes(p.read_bytes())

old = '''    float mean_abs;
} smartroom_sample_t;'''
new = '''    float mean_abs;
    uint8_t csi[512];
    uint16_t csi_bytes;
    uint8_t first_word_invalid;
} smartroom_sample_t; /* SMARTROOM_CSI_V2 */'''
assert s.count(old) == 1, 'Estrutura inesperada'
s = s.replace(old, new)

old = '''    char json[256];'''
new = '''    char json[1400];'''
assert s.count(old) == 1
s = s.replace(old, new)

start = s.index('            int n = snprintf(json, sizeof(json),')
end = s.index('            if (n > 0 && n < sizeof(json)) {', start)
replacement = r'''            int n = snprintf(json, sizeof(json),
                "{\"esp_id\":\"%s\",\"seq\":%lu,\"rssi\":%d,\"csi_len\":%d,"
                "\"sig_mode\":%d,\"mcs\":%d,\"channel\":%d,\"csi_mean_abs\":%.2f,"
                "\"first_word_invalid\":%u,\"csi_encoding\":\"hex_u8\",\"csi\":\"",
                SMARTROOM_NODE_ID, (unsigned long)sample.seq, sample.rssi,
                sample.len, sample.sig_mode, sample.mcs, sample.channel,
                (double)sample.mean_abs, (unsigned)sample.first_word_invalid);
            if (n < 0 || (size_t)n >= sizeof(json)) continue;
            static const char hex[] = "0123456789abcdef";
            for (unsigned i = 0; i < sample.csi_bytes; ++i) {
                if ((size_t)n + 4 >= sizeof(json)) break;
                uint8_t v = sample.csi[i];
                json[n++] = hex[v >> 4];
                json[n++] = hex[v & 15];
            }
            json[n++] = '"';
            json[n++] = '}';
            json[n] = 0;
'''
s = s[:start] + replacement + s[end:]
old = '''        xQueueSend(smartroom_queue, &sample, 0);'''
new = '''        sample.csi_bytes = info->len < sizeof(sample.csi) ? info->len : sizeof(sample.csi);
        sample.first_word_invalid = info->first_word_invalid;
        memcpy(sample.csi, info->buf, sample.csi_bytes);
        xQueueSend(smartroom_queue, &sample, 0);'''
assert s.count(old) == 1
s = s.replace(old, new)
p.write_text(s, encoding='utf-8')
print('Patch V2 aplicado:', p)
print('Backup:', backup)
print('Formato CSI: hex_u8, bytes brutos (até 512), 1 a cada 10 quadros')
