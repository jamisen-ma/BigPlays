import { absUrl, DEFAULT_API_BASE, hostFromHostUri, isTailnetHost, normalizeBaseUrl, parsePort, resolveApiBase } from '../apiBase'

describe('normalizeBaseUrl', () => {
  it('adds http:// and strips trailing slashes, /api, query', () => {
    expect(normalizeBaseUrl('192.168.1.20:8000')).toBe('http://192.168.1.20:8000')
    expect(normalizeBaseUrl('  http://100.64.0.5:8000/  ')).toBe('http://100.64.0.5:8000')
    expect(normalizeBaseUrl('https://mac.tail1234.ts.net/api')).toBe('https://mac.tail1234.ts.net')
    expect(normalizeBaseUrl('http://host:8000/?x=1')).toBe('http://host:8000')
  })
  it('rejects empty / garbage', () => {
    expect(normalizeBaseUrl('')).toBeNull()
    expect(normalizeBaseUrl('   ')).toBeNull()
    expect(normalizeBaseUrl(null)).toBeNull()
    expect(normalizeBaseUrl('ftp://x')).toBeNull()
    expect(normalizeBaseUrl('http://')).toBeNull()
  })
})

describe('hostFromHostUri', () => {
  it('extracts the host from LAN / Tailscale / MagicDNS / IPv6 hostUris', () => {
    expect(hostFromHostUri('192.168.1.20:8081')).toBe('192.168.1.20')
    expect(hostFromHostUri('100.101.102.103:8081')).toBe('100.101.102.103')
    expect(hostFromHostUri('mac.tail1234.ts.net:8081')).toBe('mac.tail1234.ts.net')
    expect(hostFromHostUri('[fd7a:115c::1]:8081')).toBe('[fd7a:115c::1]')
    expect(hostFromHostUri('exp://192.168.1.20:8081/--/')).toBe('192.168.1.20')
  })
  it('ignores tunnels and empty values', () => {
    expect(hostFromHostUri('abc-anonymous-8081.exp.direct')).toBeNull()
    expect(hostFromHostUri('abc.ngrok-free.app:443')).toBeNull()
    expect(hostFromHostUri('')).toBeNull()
    expect(hostFromHostUri(undefined)).toBeNull()
  })
})

describe('resolveApiBase', () => {
  const hostUri = '100.101.102.103:8081'
  it('prefers the Settings override', () => {
    expect(resolveApiBase({ override: 'http://10.0.0.2:9000/', envUrl: 'http://env:8000', hostUri }))
      .toEqual({ url: 'http://10.0.0.2:9000', source: 'override' })
  })
  it('then EXPO_PUBLIC_API_URL', () => {
    expect(resolveApiBase({ override: '', envUrl: 'https://mac.tail1234.ts.net', hostUri }))
      .toEqual({ url: 'https://mac.tail1234.ts.net', source: 'env' })
  })
  it('then the Metro host: :8765 on Tailscale, :8000 elsewhere, explicit port wins', () => {
    expect(resolveApiBase({ hostUri })).toEqual({ url: 'http://100.101.102.103:8765', source: 'metro' })
    expect(resolveApiBase({ hostUri: '100.78.97.55:8081' })).toEqual({ url: 'http://100.78.97.55:8765', source: 'metro' })
    expect(resolveApiBase({ hostUri: 'mac.tail1234.ts.net:8081' })).toEqual({ url: 'http://mac.tail1234.ts.net:8765', source: 'metro' })
    expect(resolveApiBase({ hostUri: '192.168.1.20:8081' })).toEqual({ url: 'http://192.168.1.20:8000', source: 'metro' })
    expect(resolveApiBase({ hostUri: '192.168.1.20:8081', port: 8123 })).toEqual({ url: 'http://192.168.1.20:8123', source: 'metro' })
    expect(resolveApiBase({ hostUri, port: '8000' })).toEqual({ url: 'http://100.101.102.103:8000', source: 'metro' })
    expect(resolveApiBase({ hostUri, port: 'junk' })).toEqual({ url: 'http://100.101.102.103:8765', source: 'metro' })
  })
  it('then the web page host, then 127.0.0.1', () => {
    expect(resolveApiBase({ webHostname: 'localhost' })).toEqual({ url: 'http://localhost:8000', source: 'metro' })
    expect(resolveApiBase({ hostUri: 'x.exp.direct' })).toEqual({ url: DEFAULT_API_BASE, source: 'default' })
    expect(resolveApiBase({})).toEqual({ url: 'http://127.0.0.1:8000', source: 'default' })
  })
})

describe('isTailnetHost / parsePort', () => {
  it('detects Tailscale CGNAT, IPv6 and MagicDNS hosts', () => {
    expect(isTailnetHost('100.78.97.55')).toBe(true)
    expect(isTailnetHost('100.64.0.1')).toBe(true)
    expect(isTailnetHost('100.127.255.255')).toBe(true)
    expect(isTailnetHost('100.63.0.1')).toBe(false)
    expect(isTailnetHost('100.128.0.1')).toBe(false)
    expect(isTailnetHost('[fd7a:115c:a1e0::1]')).toBe(true)
    expect(isTailnetHost('mac.tail1234.ts.net')).toBe(true)
    expect(isTailnetHost('192.168.1.20')).toBe(false)
    expect(isTailnetHost('localhost')).toBe(false)
    expect(isTailnetHost(null)).toBe(false)
  })
  it('parses ports', () => {
    expect(parsePort('8765')).toBe(8765)
    expect(parsePort(8000)).toBe(8000)
    expect(parsePort('')).toBeNull()
    expect(parsePort(undefined)).toBeNull()
    expect(parsePort('0')).toBeNull()
    expect(parsePort('70000')).toBeNull()
    expect(parsePort('80a')).toBeNull()
  })
})

describe('absUrl', () => {
  const base = 'http://100.64.0.5:8000'
  it('prefixes relative clip URLs with the API base', () => {
    expect(absUrl(base, '/clips/a.mp4')).toBe('http://100.64.0.5:8000/clips/a.mp4')
    expect(absUrl(base + '/', 'clips/a.jpg')).toBe('http://100.64.0.5:8000/clips/a.jpg')
  })
  it('passes absolute URLs through and handles null', () => {
    expect(absUrl(base, 'https://i.ytimg.com/vi/x/mqdefault.jpg')).toBe('https://i.ytimg.com/vi/x/mqdefault.jpg')
    expect(absUrl(base, '//cdn.example.com/p.jpg')).toBe('https://cdn.example.com/p.jpg')
    expect(absUrl(base, null)).toBeNull()
    expect(absUrl(base, '')).toBeNull()
  })
})
