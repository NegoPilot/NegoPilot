import { createApp } from 'vue'

import App from './App.vue'
import { router } from './router'
import 'pretendard/dist/web/variable/pretendardvariable-dynamic-subset.css'
import './styles/base.css'

createApp(App).use(router).mount('#app')
