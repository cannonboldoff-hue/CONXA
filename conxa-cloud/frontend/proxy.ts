import { clerkMiddleware, createRouteMatcher } from '@clerk/nextjs/server'
import { NextResponse } from 'next/server'

const isPublic = createRouteMatcher([
  '/',
  '/pricing',
  '/robots.txt',
  '/sitemap.xml',
  '/llms.txt',
  '/docs(.*)',
  '/claim(.*)',
  '/sign-in(.*)',
  '/sign-up(.*)',
  '/__clerk(.*)',
  '/api/v1/webhooks/(.*)',
])
// The Build Studio tour's practice company needs a Conxa sign-in, but not a workspace.
const isPractice = createRouteMatcher(['/practice(.*)'])
const isOnboarding = createRouteMatcher(['/onboarding(.*)'])

export default clerkMiddleware(async (auth, req) => {
  if (isPublic(req)) return

  const { userId, orgId } = await auth()

  if (!userId) {
    const signInUrl = new URL('/sign-in', req.url)
    signInUrl.searchParams.set('redirect_url', req.url)
    return NextResponse.redirect(signInUrl)
  }

  if (!orgId && !isOnboarding(req) && !isPractice(req)) {
    return NextResponse.redirect(new URL('/onboarding', req.url))
  }

  if (orgId && isOnboarding(req)) {
    return NextResponse.redirect(new URL('/dashboard', req.url))
  }
})

export const config = {
  matcher: [
    '/((?!_next|[^?]*\\.(?:html?|css|js(?!on)|jpe?g|webp|png|gif|svg|ttf|woff2?|ico|csv|docx?|xlsx?|zip|webmanifest)).*)',
    '/(api|trpc|__clerk)(.*)',
  ],
}
