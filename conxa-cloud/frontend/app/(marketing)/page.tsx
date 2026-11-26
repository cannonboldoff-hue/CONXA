import type { Metadata } from 'next'
import { HomeMotion } from '@/components/marketing/home/HomeMotion'
import { HomeHero } from '@/components/marketing/home/HomeHero'
import { HomeFilm } from '@/components/marketing/home/HomeFilm'
import { HomeWork } from '@/components/marketing/home/HomeWork'
import { HomeTrust } from '@/components/marketing/home/HomeTrust'
import { HomeCompare } from '@/components/marketing/home/HomeCompare'
import { HomeData } from '@/components/marketing/home/HomeData'
import { HomeShare } from '@/components/marketing/home/HomeShare'
import { HomeFit } from '@/components/marketing/home/HomeFit'
import { HomePricing } from '@/components/marketing/home/HomePricing'
import { HomeFaq } from '@/components/marketing/home/HomeFaq'
import { HomeCta } from '@/components/marketing/home/HomeCta'
import { createPublicPageMetadata } from '@/lib/siteMetadata'

export const metadata: Metadata = createPublicPageMetadata({
  title: 'CONXA',
  description:
    'Record a task once and your AI repeats it, the same steps every time, checked, on your own computer. Share it with your team or your customers.',
  path: '/',
})

export default function MarketingPage() {
  return (
    <HomeMotion>
      <HomeHero />
      <HomeFilm />
      <HomeWork />
      <HomeTrust />
      <HomeCompare />
      <HomeData />
      <HomeShare />
      <HomeFit />
      <HomePricing />
      <HomeFaq />
      <HomeCta />
    </HomeMotion>
  )
}
