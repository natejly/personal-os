/**
 * Host checks for the offscreen page loader. Chromium dials several spellings that Node's isIP
 * does not treat as addresses (decimal 2130706433, octal 0177.0.0.1, 127.1, 0x7f.0.0.1), and a
 * public page can redirect to a name that resolves on the LAN. Both have to be refused before
 * the hidden window connects.
 */
import { lookup } from 'dns/promises'
import { BlockList, isIP } from 'net'

const v4 = new BlockList()
v4.addSubnet('0.0.0.0', 8, 'ipv4')
v4.addSubnet('10.0.0.0', 8, 'ipv4')
v4.addSubnet('127.0.0.0', 8, 'ipv4')
v4.addSubnet('169.254.0.0', 16, 'ipv4')
v4.addSubnet('172.16.0.0', 12, 'ipv4')
v4.addSubnet('192.168.0.0', 16, 'ipv4')
v4.addSubnet('100.64.0.0', 10, 'ipv4')
v4.addRange('224.0.0.0', '255.255.255.255', 'ipv4')

const v6 = new BlockList()
v6.addAddress('::', 'ipv6')
v6.addAddress('::1', 'ipv6')
v6.addSubnet('fc00::', 7, 'ipv6')
v6.addSubnet('fe80::', 10, 'ipv6')
v6.addSubnet('fec0::', 10, 'ipv6') // site-local; Node and Python 3.10 both treat it as global
v6.addSubnet('ff00::', 8, 'ipv6')
v6.addSubnet('64:ff9b:1::', 48, 'ipv6') // local-use NAT64
v6.addSubnet('2001::', 32, 'ipv6') // Teredo

function ipv4Number(part: string): number | null {
  if (!part) return null
  let rest = part
  let radix = 10
  if (rest.length >= 2 && rest[0] === '0' && (rest[1] === 'x' || rest[1] === 'X')) {
    radix = 16
    rest = rest.slice(2)
    if (!/^[0-9a-f]+$/i.test(rest)) return null
  } else if (rest.length >= 2 && rest[0] === '0') {
    radix = 8
    rest = rest.slice(1)
    if (!/^[0-7]+$/.test(rest)) return null
  } else if (!/^[0-9]+$/.test(rest)) {
    return null
  }
  const n = parseInt(rest, radix)
  return Number.isSafeInteger(n) ? n : null
}

/** Dotted quad if Chrome would treat `host` as an IPv4 literal, else null. */
export function chromeIpv4(host: string): string | null {
  let text = host.trim().replace(/^\[|\]$/g, '')
  if (text.endsWith('.')) text = text.slice(0, -1)
  const parts = text.split('.')
  if (parts.length === 0 || parts.length > 4) return null
  const nums: number[] = []
  for (const part of parts) {
    const n = ipv4Number(part)
    if (n === null) return null
    nums.push(n)
  }
  for (let i = 0; i < nums.length - 1; i++) {
    if (nums[i] > 255) return null
  }
  const last = nums[nums.length - 1]
  if (last >= 256 ** (4 - (nums.length - 1))) return null
  let value = last
  for (let i = 0; i < nums.length - 1; i++) value += nums[i] * 256 ** (3 - i)
  return [(value >>> 24) & 255, (value >>> 16) & 255, (value >>> 8) & 255, value & 255].join('.')
}

function hextets(addr: string): number[] | null {
  const text = addr.split('%')[0].toLowerCase()
  if (text.includes('.')) return null
  const halves = text.split('::')
  if (halves.length > 2) return null
  const side = (s: string): number[] | null => {
    if (!s) return []
    const out: number[] = []
    for (const part of s.split(':')) {
      if (!/^[0-9a-f]{1,4}$/.test(part)) return null
      out.push(parseInt(part, 16))
    }
    return out
  }
  const left = side(halves[0])
  if (!left) return null
  if (halves.length === 1) return left.length === 8 ? left : null
  const right = side(halves[1])
  if (!right) return null
  const missing = 8 - left.length - right.length
  if (missing < 1) return null
  return [...left, ...Array(missing).fill(0), ...right]
}

function dotted(hi: number, lo: number): string {
  return `${(hi >> 8) & 255}.${hi & 255}.${(lo >> 8) & 255}.${lo & 255}`
}

/** IPv4 carried inside a v6 literal the blocklist does not classify on its own. */
function embeddedV4(addr: string): string | null {
  const h = hextets(addr)
  if (!h) return null
  const [a, b, c, d, e, f, g, i] = h
  if (a === 0 && b === 0 && c === 0 && d === 0 && e === 0 && f === 0xffff) return dotted(g, i) // ::ffff:7f00:1
  if (a === 0 && b === 0 && c === 0 && d === 0 && e === 0 && f === 0 && (g !== 0 || i !== 0)) return dotted(g, i)
  if (a === 0x2002) return dotted(b, c) // 6to4
  if (a === 0x64 && b === 0xff9b && c === 0 && d === 0 && e === 0 && f === 0) return dotted(g, i) // 64:ff9b::/96
  return null
}

export function isPrivateIp(ip: string): boolean {
  const bare = ip.split('%')[0]
  const low = bare.toLowerCase()
  const mapped = low.startsWith('::ffff:') ? low.slice('::ffff:'.length) : ''
  if (mapped && isIP(mapped) === 4) return isPrivateIp(mapped)
  if (isIP(bare) === 4) return v4.check(bare, 'ipv4')
  if (isIP(low) === 6) {
    if (v6.check(low, 'ipv6')) return true
    const embed = embeddedV4(low)
    if (!embed) return false
    const h = hextets(low)
    // Deprecated IPv4-compatible addresses (::/96) are not public, whatever they embed.
    if (h && h[0] === 0 && h[5] === 0) return true
    return isPrivateIp(embed)
  }
  return true
}

function isLoopbackV4(dotted: string): boolean {
  return dotted === '0.0.0.0' || dotted.startsWith('127.')
}

/**
 * Loopback and the unspecified address, including the spellings Chrome dials as 127/8.
 * Private LAN ranges are not loopback: a page on the LAN is not one of the app's own services.
 */
export function isLoopbackHost(host: string): boolean {
  const h = host.replace(/^\[|\]$/g, '').toLowerCase().replace(/\.$/, '')
  if (!h || h === 'localhost' || h.endsWith('.localhost')) return true
  const chrome = chromeIpv4(h)
  if (chrome) return isLoopbackV4(chrome)
  const bare = h.split('%')[0]
  if (bare === '::1' || bare === '::') return true
  const mapped = bare.startsWith('::ffff:') ? bare.slice('::ffff:'.length) : ''
  if (mapped) return isLoopbackHost(mapped)
  if (isIP(bare) === 4) return isLoopbackV4(bare)
  if (isIP(bare) === 6) {
    const embed = embeddedV4(bare)
    if (embed && isLoopbackV4(embed)) return true
  }
  return false
}

/** Literal private, loopback, link-local and Chrome-style spellings of those. Names return false. */
export function isPrivateHost(host: string): boolean {
  const h = host.replace(/^\[|\]$/g, '').toLowerCase().replace(/\.$/, '')
  if (!h || h === 'localhost' || h.endsWith('.localhost') || h.endsWith('.local')) return true
  const chrome = chromeIpv4(h)
  if (chrome) return isPrivateIp(chrome)
  const bare = h.split('%')[0]
  if (isIP(bare)) return isPrivateIp(bare)
  return false
}

async function resolveHost(host: string): Promise<string[]> {
  const rows = await lookup(host, { all: true, verbatim: true })
  return rows.map((row) => row.address)
}

/**
 * Resolve through a Chromium session's own host resolver, so the check and the connect that follows
 * share one host cache entry. A separate Node lookup lets a TTL-0 name answer public here and private there.
 */
export const sessionResolver = (ses: { resolveHost(host: string): Promise<{ endpoints: { address: string }[] }> }) =>
  async (host: string): Promise<string[]> => (await ses.resolveHost(host)).endpoints.map((e) => e.address)

/** True when this host must not be connected to. DNS failures are refused. */
export async function hostBlocked(host: string, resolve: (host: string) => Promise<string[]> = resolveHost): Promise<boolean> {
  const h = host.replace(/^\[|\]$/g, '')
  if (!h || isPrivateHost(h)) return true
  if (isIP(h)) return false
  try {
    const addrs = await resolve(h)
    return addrs.length === 0 || addrs.some((addr) => isPrivateIp(addr))
  } catch {
    return true
  }
}
