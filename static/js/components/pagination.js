import { logger } from '../logger';

export class Pagination {

    constructor(previous, next, count, page_size, page_number) {
        this.previous = previous;
        this.next = next;
        this.count = count;
        this.page_size = page_size;
        this.page_number = page_number;
        this.number_of_surrounding_pages = 2;
    }

    render_link(linkUrl, page) {

        linkUrl.replace(/page=\d+/, `page=${page}`);
        logger.log('debug', 'link = %s', linkUrl);

        return `
  <li class="page-item ${page == this.page_number ? 'active' : ''}">
    <a class="page-link" href="${linkUrl}">${page}</a>
  </li>`;
    }

    render() {

        let html = `
<ul class="pagination">`;

        if (this.previous != null) {

            html += `
  <li class="page-item">
    <a class="page-link" href="${this.previous}" aria-label="Previous">
      <span aria-hidden="true">&laquo;</span>
    </a>
  </li>`;

        } else {

            html += `
  <li class="page-item disabled">
    <a class="page-link" href="#" aria-label="Previous">
      <span aria-hidden="true">&laquo;</span>
    </a>
  </li>`;

        }

        const number_of_pages = (this.count/this.page_size|0);
        logger.log('debug', 'number_of_pages = %d', number_of_pages);

        let linkUrl = this.previous != null ? this.previous : this.next;
        logger.log('debug', 'link = %s', linkUrl);

        // calculate pages
        const first_page = Math.max(this.page_number - this.number_of_surrounding_pages, 1);
        const last_page = Math.min(this.page_number + this.number_of_surrounding_pages, number_of_pages);

        if (first_page !== 1) {

            html += this.render_link(linkUrl, 1);

            if (first_page !== 2) {
                html += `
  <li class="page-item disabled">
    <a class="page-link" href="#"><span aria-hidden="true">&hellip;</span></a>
  </li>`;

            }

        }

        for (let page = first_page; page <= last_page; page++) {

            html += this.render_link(linkUrl, page);

        }

        if (last_page !== number_of_pages) {

            if (last_page !== number_of_pages - 1) {

                html += `
  <li class="page-item disabled">
    <a class="page-link" href="#"><span aria-hidden="true">&hellip;</span></a>
  </li>`;
            }

            html += this.render_link(linkUrl, number_of_pages);

        }

        if (this.next!= null) {

            html += `
  <li class="page-item">
    <a class="page-link" href="${this.next}" aria-label="Next">
      <span aria-hidden="true">&raquo;</span>
    </a>
  </li>`;

        } else {

            html += `
  <li class="page-item disabled">
    <a class="page-link" href="#" aria-label="Next">
      <span aria-hidden="true">&raquo;</span>
    </a>
  </li>`;

        }

        html += `
</ul>`;

        return html;

    }

}

