import type { MetadataRoute } from 'next'
import { SITE_ORIGIN, absoluteUrl } from '@/lib/siteMetadata'

export default function robots(): MetadataRoute.Robots {
  return {
    rules: {
      userAgent: '*',
      allow: ['/', '/docs', '/docs/'],
      disallow: [
        '/api/',
        '/billing',
        '/dashboard',
        '/packages',
        '/compile',
        '/build-installer',
        '/settings',
        '/team',
        '/test',
        '/practice',
      ],
    },
    sitemap: absoluteUrl('/sitemap.xml'),
    host: SITE_ORIGIN,
  }
}
