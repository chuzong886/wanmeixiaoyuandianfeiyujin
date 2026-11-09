<!-- 系统logo -->
<template>
  <div class="flex-cc">
    <img :style="logoStyle" :src="brand?.logo || defaultLogo" alt="logo" class="w-full h-full" />
  </div>
</template>

<script setup lang="ts">
  const defaultLogo = '/tianshu-logo.png'
  const energyBrand = inject<any>('energyBrand', null)
  const brand = computed(() => energyBrand?.value)
  defineOptions({ name: 'ArtLogo' })

  interface Props {
    /** logo 大小 */
    size?: number | string
  }

  const props = withDefaults(defineProps<Props>(), {
    size: 36
  })

  const logoStyle = computed(() => ({ width: `${props.size}px` }))
</script>
