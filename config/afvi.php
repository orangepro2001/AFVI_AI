<?php

return [
    'data_home' => env('AFVI_DATA_HOME', '/srv/afvi-ai-data'),

    /*
     * Public mount point on the tailnet. Routes are registered both at "/" and under this prefix.
     */
    'public_prefix' => env('AFVI_PUBLIC_PREFIX', '/afvi_ai'),

    /*
     * Review lists written by `python -m afvi_ai.score`, below data_home/reports/review/<run>.
     */
    'review_run' => env('AFVI_REVIEW_RUN', 'current'),

    /*
     * File queue shared with the image worker (`python -m afvi_ai.vision.serve`).
     */
    'vision_queue' => env('AFVI_VISION_QUEUE', storage_path('app/vision')),
    'vision_timeout_seconds' => (float) env('AFVI_VISION_TIMEOUT', 15),
];
